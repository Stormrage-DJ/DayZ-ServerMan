"""Export, import and copy of mission map plans into the destination draft; no game file is written (D7, R18)."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from ..domain.mission_map_merge import (
    Availability, Candidate, TransferMode, combine, compatibility, read_candidate,
)
from ..domain.mission_map_plan import PlanForm, parse_plan, to_portable
from ..domain.mission_map_prototype import is_prototype_export, migrate
from ..domain.mission_map_values import (
    MAX_DOCUMENT_BYTES, document_error, refuse_json_constant, unique_json_keys,
)
from ..domain.models import RevisionConflict
from .mission_map_plans import MissionMapPlanService


class TransferRefused(ValueError):
    """Raised when an export, import or copy cannot reach its plan at all.

    `reason` is the closed reason of D16: `no_valid_plan`, `source_is_destination`, `plan_limit` or
    `mission_path_changed`. `side` names the plan that refused: `export`, `destination` or `source`.
    """

    def __init__(self, message: str, reason: str, side: str, counts: dict[str, int] | None = None) -> None:
        """Keep the developer message, the closed reason, the side and, for a plan limit, the result counts."""
        self.reason = reason
        self.side = side
        self.counts = counts or {}
        super().__init__(message)


class ConfirmationRequired(RuntimeError):
    """Raised when the preview needs the admin's confirmation: placement review, exclusions or unmapped items."""

    def __init__(self, report: dict[str, Any]) -> None:
        """Store the preview that the admin must confirm."""
        self.report = report
        super().__init__("the import needs confirmation of its preview")


class MissionMapTransferService:
    """Move plans between files, profiles and the draft through one preview; every change is one guarded save."""

    def __init__(self, plans: MissionMapPlanService) -> None:
        """Store the plan service; each preview and commit gets the destination availability check as a parameter."""
        self._plans = plans

    def export_text(self, profile_id: object) -> str:
        """Return the portable form of the shown plan as indented canonical JSON with a final newline."""
        return self.export_plan(profile_id)["text"]

    def export_plan(self, profile_id: object) -> dict[str, Any]:
        """Return the portable text of the shown plan and the revision of the read (D16)."""
        loaded = self._plans.load(profile_id)
        if loaded["state"] != "VALID":
            raise TransferRefused(f"there is no valid plan to export ({loaded['state']})", "no_valid_plan", "export")
        # The portable form has no stored-only field and no local image path
        text = json.dumps(to_portable(loaded["plan"]), ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n"
        return {"text": text, "revision": loaded["revision"]}

    def preview_import(self, profile_id: object, text: str | bytes, mode: object,
                       availability: Availability | None = None) -> dict[str, Any]:
        """Return the compatibility preview of importing a file text into the profile's draft."""
        loaded = self._destination(profile_id)
        candidate = self._candidate_from_text(text, loaded["plan"])
        return self._preview(loaded, candidate, TransferMode(mode), availability)

    def import_plan(self, profile_id: object, text: str | bytes, mode: object, expected_revision: object,
                    confirmed: bool = False, availability: Availability | None = None) -> dict[str, Any]:
        """Import a file text into the draft as one revision-guarded save."""
        loaded = self._destination(profile_id)
        candidate = self._candidate_from_text(text, loaded["plan"])
        return self._commit(profile_id, loaded, candidate, TransferMode(mode), expected_revision, confirmed,
                            availability)

    def preview_copy(self, source_profile_id: object, profile_id: object, mode: object,
                     availability: Availability | None = None) -> dict[str, Any]:
        """Return the compatibility preview of copying another profile's plan into the draft."""
        loaded = self._destination(profile_id)
        candidate = self._copy_candidate(source_profile_id, loaded)
        return self._preview(loaded, candidate, TransferMode(mode), availability)

    def copy_plan(self, source_profile_id: object, profile_id: object, mode: object, expected_revision: object,
                  confirmed: bool = False, availability: Availability | None = None) -> dict[str, Any]:
        """Copy another profile's plan into the draft as one revision-guarded save; the source stays unchanged."""
        loaded = self._destination(profile_id)
        candidate = self._copy_candidate(source_profile_id, loaded)
        return self._commit(profile_id, loaded, candidate, TransferMode(mode), expected_revision, confirmed,
                            availability)

    def _destination(self, profile_id: object) -> dict[str, Any]:
        """Return the destination draft; it must be a valid, writable plan of the active association."""
        loaded = self._plans.load(profile_id)
        if loaded["read_only"]:
            raise TransferRefused("the destination mission path changed; adopt the earlier plan before an import",
                                  "mission_path_changed", "destination")
        if loaded["state"] != "VALID":
            raise TransferRefused(f"the destination has no valid saved plan ({loaded['state']})", "no_valid_plan",
                                  "destination")
        return loaded

    def _candidate_from_text(self, text: str | bytes, destination: dict[str, Any]) -> Candidate:
        """Read strict JSON of at most 8 MiB and validate it as a plan document for the destination.

        A Pripyat prototype export has no format marker; it takes the one-time R21 migration instead and keeps
        the list of its unmapped items for the preview.
        """
        value = _json_value(text)
        if is_prototype_export(value):
            migration = migrate(value)
            return dataclasses.replace(read_candidate(migration.document, destination), unmapped=migration.unmapped)
        return read_candidate(value, destination)

    def _copy_candidate(self, source_profile_id: object, loaded: dict[str, Any]) -> Candidate:
        """Read the active plan of another profile; it keeps its background path, because both are on this machine."""
        source = self._plans.load(source_profile_id)
        if source["association"]["profile_id"] == loaded["association"]["profile_id"]:
            raise TransferRefused("copy needs another profile as its source", "source_is_destination", "source")
        # A source in the path-change state has no plan of its own active association
        if source["read_only"]:
            raise TransferRefused("the source mission path changed; its active association has no plan to copy",
                                  "mission_path_changed", "source")
        if source["state"] != "VALID":
            raise TransferRefused(f"the source has no valid saved plan ({source['state']})", "no_valid_plan", "source")
        candidate = read_candidate(source["plan"], loaded["plan"], keep_path=True)
        return dataclasses.replace(candidate, discarded=[])

    def _preview(self, loaded: dict[str, Any], candidate: Candidate, mode: TransferMode,
                 availability: Availability | None) -> dict[str, Any]:
        """Return the compatibility report with the destination revision that an apply must name."""
        report = compatibility(loaded["plan"], candidate, mode, availability)
        return {**report, "destination_revision": loaded["revision"]}

    def _commit(self, profile_id: object, loaded: dict[str, Any], candidate: Candidate, mode: TransferMode,
                expected_revision: object, confirmed: bool, availability: Availability | None) -> dict[str, Any]:
        """Check the revision, combine once, check the preview rules and the whole result, then save under the guard.

        A stale revision refuses first: its report is not the report that the admin saw (Architect 2026-10-17
        03:38:11 in evidence 10.33). The save keeps its own guard under the association lock.
        """
        if expected_revision != loaded["revision"]:
            raise RevisionConflict("the destination plan changed after the preview")
        combined = combine(loaded["plan"], candidate, mode)
        report = compatibility(loaded["plan"], candidate, mode, availability, combined=combined)
        # A plan-level limit refuses everything; nothing is excluded or reviewed without confirmation
        if report["refusal"] is not None:
            raise TransferRefused(report["refusal"], "plan_limit", "destination",
                                  {"objects": report["result_object_count"], "vertices": report["result_vertex_count"]})
        if report["confirmation_required"] and not confirmed:
            raise ConfirmationRequired(report)
        result = parse_plan(combined[0], PlanForm.STORED)
        saved = self._plans.save(profile_id, result, expected_revision)
        return {"revision": saved["revision"], "plan": saved["plan"], "report": report}


def _json_value(text: str | bytes) -> object:
    """Return the JSON value of a file text: UTF-8, at most 8 MiB, no NaN or infinity, no repeated key."""
    try:
        data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    except UnicodeError as error:
        raise document_error("the plan file is not UTF-8 text") from error
    if len(data) > MAX_DOCUMENT_BYTES:
        raise document_error("the plan file is larger than 8 MiB")
    try:
        return json.loads(data.decode("utf-8"), parse_constant=refuse_json_constant,
                          object_pairs_hook=unique_json_keys)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise document_error(f"the plan file is not valid UTF-8 JSON: {error}") from error
