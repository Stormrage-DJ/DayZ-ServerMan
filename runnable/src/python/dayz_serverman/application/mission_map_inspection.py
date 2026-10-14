"""Inspect start, continue and cancel: S1 validation, the trusted context, job publication and error binding.

The start validates the plan the way save does, with no reader and no lock. The
mission read pass and the final proof then run in reader-held slices (A181).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from ..adapters.windows.server_folder_lock import READER_BUSY, ServerFolderBusy
from ..adapters.windows.shared_files import read_bytes_shared
from ..bridge.contracts import ErrorCode
from ..bridge.facade import ApplicationCallError
from ..domain.mission_map_plan import PlanForm, configuration_fingerprint, parse_plan
from ..domain.mission_map_terrain import MISSION_FILE_UNREADABLE, TerrainDataError
from ..repositories.mission_map_layout import TargetClass, target_key
from ..repositories.mission_map_plans import PlanRecordInvalid, PlanState, check_size
from .mission_map_coordinator_errors import exact_parameters, refusal, translate
from .mission_map_inspection_base import BaselineReader
from .mission_map_inspection_jobs import InspectionJob, InspectionJobs, InspectionUnknown
from .mission_map_inspection_run import InspectionRun, InspectTarget, PlanSource, STALE, reading
from .mission_map_plans import AssociationMismatch, MissionMapPlanService, _check_association, _runtime_key, plan_store
from .mission_map_reader_port import ReaderPort, ReaderUnsupported


# Reader wait of the start's context step; the same bound as an observer load (A13)
START_READER_WAIT_SECONDS = 30.0
# A plan digest that no file has; a plan that changed while it was read is stale at the first recheck
_UNMATCHED = ""


@dataclass(frozen=True)
class InspectContext:
    """Trusted facts of one start: profile, resolved mission folder and the active and shown association."""

    profile_id: str
    mission: Path
    mission_key: str
    shown_key: str
    # Relative mission root of the shown association: the active root, or the old root in the path-change state
    shown_root: str

    def shown_association(self) -> dict[str, Any]:
        """Return the association that load shows, in the form that save's association check takes."""
        return {"profile_id": self.profile_id, "mission_root": self.shown_root, "mission_key": self.shown_key}


@dataclass(frozen=True)
class PlanBinding:
    """The saved plan file and its raw SHA-256 as validated at start; a draft has no file binding.

    baseline is the manifest digest of the working baseline at start, or None without one; it joins the recheck.
    """

    path: Path | None
    digest: str | None
    baseline: str | None = None


class MissionMapInspection:
    """Serve the three Inspect bridge methods of one session."""

    def __init__(self, plans: MissionMapPlanService, reader: ReaderPort, *, jobs: InspectionJobs | None = None,
                 clock: Callable[[], float] = monotonic) -> None:
        """Keep the plan service, the reader port of this session and its job registry."""
        self._plans = plans
        self._reader = reader
        self._jobs = jobs or InspectionJobs()
        self._clock = clock

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        """Return start, continue and cancel for registration."""
        return {"inspect_mission_map": self.inspect_mission_map,
                "continue_mission_map_inspection": self.continue_mission_map_inspection,
                "cancel_mission_map_inspection": self.cancel_mission_map_inspection}

    def inspect_mission_map(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Validate the plan synchronously, create the job and start the capture pass in the same call."""
        exact_parameters(parameters, {"profile_id", "plan", "target"})
        # The arrival number comes before validation and any reader wait (Inspect Start Order)
        arrival = self._jobs.arrive(parameters["profile_id"])
        try:
            # S1: an invalid draft refuses with save's errors before any reader or mission file
            draft = None if parameters["plan"] is None else validated_draft(parameters["plan"])
            with self._reader.hold(START_READER_WAIT_SECONDS):
                context, runtime_key = self._resolved(parameters["profile_id"])
                # Save's association rule; the runtime key resolves a folder in the DayZ root, so it runs here.
                # The path-change state keeps Inspect available (Product Owner, QA-4.2-1): no path-change refusal.
                if draft is not None:
                    _check_association(draft["association"], context.shown_association(), runtime_key)
            if draft is None:
                plan, source, binding = self._saved(context)
            else:
                # Save checks the document size after the association, so Inspect keeps that order
                check_size(draft)
                plan, binding = draft, PlanBinding(None, None)
                source = PlanSource("draft", None, configuration_fingerprint(draft))
            target = inspect_target(parameters["target"], plan)
            # The 8.6 base: the working baseline when the association has one (6.9 ruling)
            manifest, base = self._baseline(context).read(target.object_id is not None)
            binding = PlanBinding(binding.path, binding.digest, manifest)
        except Exception as error:
            _raise_bound(error)
        run = InspectionRun(context.mission, plan, source, target, self._reader,
                            lambda: self._fresh(context, binding), clock=self._clock, base=base)
        try:
            job = self._jobs.create(context.profile_id, run, lambda job_id: _shape(job_id, reading()), arrival)
        except InspectionUnknown:
            # A newer Start of this editor arrived meanwhile; its job stays live
            raise _unknown() from None
        return self._advance(job)

    def continue_mission_map_inspection(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Advance a live job by one call; a simultaneous continue returns the last published progress."""
        exact_parameters(parameters, {"profile_id", "job_id"})
        try:
            job, progress = self._jobs.claim(parameters["profile_id"], parameters["job_id"])
        except InspectionUnknown:
            raise _unknown() from None
        if progress is not None:
            return progress
        return self._advance(job)

    def cancel_mission_map_inspection(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        """Retire a live job; this opens no file, so the call is reader-exempt."""
        exact_parameters(parameters, {"profile_id", "job_id"})
        try:
            self._jobs.cancel(parameters["profile_id"], parameters["job_id"])
        except InspectionUnknown:
            raise _unknown() from None
        return {"cancelled": True}

    def _advance(self, job: InspectionJob) -> dict[str, Any]:
        """Run one call of the job and publish its answer only while its generation is current."""
        try:
            answer = job.run.advance()
        except Exception as error:
            # Every failure retires its generation; a retired generation answers with the lost token
            if not self._jobs.fail(job):
                raise _unknown() from error
            _raise_bound(error)
        except BaseException:
            self._jobs.fail(job)
            raise
        shaped = _shape(job.job_id, answer)
        if not self._jobs.settle(job, shaped, terminal=answer["status"] != "running"):
            raise _unknown()
        return shaped

    def _resolved(self, profile_id: object) -> tuple[InspectContext, Callable[[], str]]:
        """Resolve profile, settings, root and association as load does; the caller holds the reader side.

        Also return save's lazy runtime-key check of the profile's runtime folder.
        """
        profile, root, relative, mission = self._plans._resolve(profile_id)
        key = target_key(TargetClass.MISSION, mission)
        matches = self._plans._path_change({"profile_id": profile.values.profile_id, "mission_root": relative,
                                            "mission_key": key})
        # In the path-change state load shows the newest old plan; Inspect reads the same plan
        shown_key, shown_root = (matches[0]["mission_key"], matches[0]["mission_root"]) if matches else (key, relative)
        context = InspectContext(profile.values.profile_id, mission, key, shown_key, shown_root)
        return context, lambda: _runtime_key(root, profile)

    def _context(self, profile_id: object) -> InspectContext:
        """Return the trusted context only; the caller holds the reader side."""
        return self._resolved(profile_id)[0]

    def _saved(self, context: InspectContext) -> tuple[dict[str, Any], PlanSource, PlanBinding]:
        """Load and validate the saved plan that load shows, with save's and load's translations."""
        store = plan_store(self._plans._area, context.profile_id, context.shown_key, self._plans._staging)
        before = raw_digest(store.path)
        inspection = store.inspect()
        after = raw_digest(store.path)
        if inspection.state is PlanState.MISSING:
            raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", "No saved plan is available to inspect.")
        if inspection.state is not PlanState.VALID:
            raise PlanRecordInvalid(inspection)
        plan = inspection.plan
        if (plan["association"]["profile_id"], plan["association"]["mission_key"]) != (
                context.profile_id, context.shown_key):
            raise AssociationMismatch("The saved plan names another profile or mission association.")
        binding = PlanBinding(store.path, before if before == after else _UNMATCHED)
        return plan, PlanSource("saved", inspection.revision, None), binding

    def _fresh(self, context: InspectContext, binding: PlanBinding) -> bool:
        """Recheck the trusted context, the saved plan bytes and the working baseline manifest digest.

        A failed resolution raises its refusal.
        """
        if self._context(context.profile_id) != context:
            return False
        if binding.path is not None and raw_digest(binding.path) != binding.digest:
            return False
        return self._baseline(context).digest() == binding.baseline

    def _baseline(self, context: InspectContext) -> BaselineReader:
        """Return the reader of the working baseline of the shown association."""
        return BaselineReader(self._plans._area, context.profile_id, context.shown_key)


def validated_draft(raw: object) -> dict[str, Any]:
    """Validate a stored-form draft with save's checks before its association, and return the parsed plan.

    Save checks the association, then the document size; the start keeps that order after this step.
    """
    background = raw.get("background") if isinstance(raw, dict) else None
    if isinstance(background, dict) and background.get("path") is not None:
        message = "A plan sent to save must not name an image path. Choose the image in the map editor."
        raise refusal(ErrorCode.INVALID_REQUEST, "invalid_document", message,
                      problems=[{"object_id": None, "rule": "background_path_not_accepted", "message": message}])
    return parse_plan(raw, PlanForm.STORED)


def inspect_target(raw: object, plan: dict[str, Any]) -> InspectTarget:
    """Return a point with finite coordinates or an object of the validated plan, else refuse invalid_target."""
    if isinstance(raw, dict) and set(raw) == {"point"}:
        point = raw["point"]
        if isinstance(point, dict) and set(point) == {"x", "z"} and all(_finite(point[name]) for name in "xz"):
            return InspectTarget(point=(point["x"], point["z"]))
    if isinstance(raw, dict) and set(raw) == {"object"} and isinstance(raw["object"], str):
        if any(item["id"] == raw["object"] for item in plan["objects"]):
            return InspectTarget(object_id=raw["object"])
    raise refusal(ErrorCode.INVALID_REQUEST, "invalid_target",
                  "Inspect needs a point on the map or an object of this plan.")


def raw_digest(path: Path) -> str | None:
    """Return the SHA-256 of the exact file bytes, or None when the file is absent."""
    try:
        return hashlib.sha256(read_bytes_shared(path)).hexdigest()
    except FileNotFoundError:
        return None


def _finite(value: object) -> bool:
    """Report a finite JSON number; a boolean is not a number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _shape(job_id: str, answer: dict[str, Any]) -> dict[str, Any]:
    """Return the public answer: running with the job progress, done with the result, or stale and ended."""
    if answer["status"] == "running":
        return {"status": "running", "job": {"id": job_id, "phase": answer["phase"],
                                             "rows_done": answer["rows_done"], "rows_total": answer["rows_total"]}}
    if answer == STALE:
        return {"status": "stale", "job": {"id": job_id, "ended": True}}
    return {"status": "done", "result": answer["result"]}


def _unknown() -> ApplicationCallError:
    """Return the lost-token refusal."""
    return refusal(ErrorCode.INVALID_REQUEST, "inspection_unknown", "The read stopped. Start Inspect again.")


def _raise_bound(error: Exception) -> None:
    """Raise the bridge binding of a failure; an unexpected error is raised unchanged."""
    bound = _binding(error)
    if bound is error:
        raise error
    raise bound from error


def _binding(error: Exception) -> BaseException:
    """Map an Inspect failure to its existing bridge binding; an unexpected error stays an internal failure."""
    if isinstance(error, ApplicationCallError):
        return error
    if isinstance(error, ServerFolderBusy):
        return ApplicationCallError(ErrorCode.CONTROL_CONFLICT, READER_BUSY, retryable=True)
    if isinstance(error, TerrainDataError):
        if error.reason == MISSION_FILE_UNREADABLE:
            return refusal(ErrorCode.STORAGE_FAILURE, error.reason, str(error), retryable=True)
        return refusal(ErrorCode.UNSUPPORTED_SNAPSHOT_CONTENT, error.reason, str(error))
    # A read never borrows the observer-save wording of PermissionError
    if isinstance(error, PermissionError):
        return refusal(ErrorCode.STORAGE_FAILURE, "storage_failure", "The map editor data could not be read.",
                       retryable=True)
    if isinstance(error, (ReaderUnsupported, MemoryError)):
        return error
    try:
        return translate(error)
    except Exception:
        return error
