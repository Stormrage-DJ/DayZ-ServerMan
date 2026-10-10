"""Plan record of one association folder: inspection, revision-guarded save, set aside and save evidence (D2)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import replace_file
from ..domain.mission_map_plan import PlanForm, canonical_json, parse_plan
from ..domain.mission_map_values import (
    IDENTIFIER, MAX_DOCUMENT_BYTES, TARGET_KEY, PlanProblem, PlanValidationError, PlanVersionError,
)
from ..domain.models import RecordState, RepositoryError, RevisionConflict
from .json_store import StagingPolicy, VersionedJsonRepository
from .mission_map_layout import PLAN_FILE, PLAN_STAGING_GLOB, RETIRED_FILE, interrupted_name, set_aside_name


# Field set of a retired-folder marker that task 10 adoption writes
RETIRED_FIELDS = frozenset(("operation_id", "retired_at", "new_mission_key"))
# UTC time of a retired marker, for example 2026-10-10T12:30:00Z
UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class PlanState(str, Enum):
    """State of a stored plan record; every state except VALID and MISSING is invalid saved data."""

    VALID = "VALID"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    FUTURE_SCHEMA = "FUTURE_SCHEMA"
    NEWER_PLAN = "NEWER_PLAN"
    INVALID_PLAN = "INVALID_PLAN"


@dataclass(frozen=True)
class PlanInspection:
    """What one plan record holds, with the evidence of interrupted saves beside it."""

    state: PlanState
    path: Path
    revision: int | None = None
    plan: dict[str, Any] | None = None
    # Relative mission root that the record names, also when the plan itself is invalid
    mission_root: str | None = None
    detail: str | None = None
    problems: tuple[PlanProblem, ...] = ()
    # Leftover plan-save staging files; an owner renames them to evidence names before its next save
    leftovers: tuple[Path, ...] = ()


class PlanRecordInvalid(RepositoryError):
    """Raised when invalid saved data would be overwritten; only set aside ends that state."""

    def __init__(self, inspection: PlanInspection) -> None:
        """Store the inspection that names the invalid state."""
        self.inspection = inspection
        super().__init__(f"the saved plan is {inspection.state.value}: {inspection.detail or 'not valid'}")


class MissionMapPlanStore:
    """Read and publish the plan record of one association folder through the versioned JSON envelope."""

    def __init__(self, folder: Path, *, staging: StagingPolicy = StagingPolicy.OWNER) -> None:
        """Bind the store to one association folder; observers ignore staging files and write nothing."""
        self.folder = folder
        self.path = folder / PLAN_FILE
        self.staging = staging
        self._record = VersionedJsonRepository(self.path, 1, staging=staging)

    def inspect(self) -> PlanInspection:
        """Classify the record as valid, missing, or one of the invalid states, without changing it."""
        # Read the staging evidence and the envelope; neither read changes a file
        leftovers = self.leftovers()
        record = self._record.inspect()
        document = record.document
        # A missing record, or staging files without a record, is a plan that was never saved
        if document is None and record.state in (RecordState.MISSING, RecordState.INTERRUPTED_WRITE):
            return PlanInspection(PlanState.MISSING, self.path, leftovers=leftovers)
        if record.state is RecordState.FUTURE_SCHEMA or document is None:
            state = PlanState.FUTURE_SCHEMA if record.state is RecordState.FUTURE_SCHEMA else PlanState.CORRUPT
            return PlanInspection(state, self.path, detail=record.detail, leftovers=leftovers)
        # The envelope is readable: the record holds only the plan field
        raw = document.fields.get("plan")
        found = _mission_root(raw)
        common = {"revision": document.revision, "mission_root": found, "leftovers": leftovers}
        if set(document.fields) != {"plan"}:
            return PlanInspection(PlanState.CORRUPT, self.path, detail="the record must hold only the plan", **common)
        # Validate the plan document by D1, including its size
        try:
            plan = parse_plan(raw, PlanForm.STORED)
            check_size(plan)
        except PlanVersionError as error:
            return PlanInspection(PlanState.NEWER_PLAN, self.path, detail=str(error), **common)
        except PlanValidationError as error:
            return PlanInspection(PlanState.INVALID_PLAN, self.path, detail=str(error), problems=error.problems,
                                  **common)
        return PlanInspection(PlanState.VALID, self.path, plan=plan, **common)

    def save(
        self, plan: dict[str, Any], expected_revision: int | None, moment: datetime,
    ) -> tuple[int, tuple[Path, ...]]:
        """Publish a plan from parse_plan under the revision guard; return the revision and moved evidence."""
        # Refuse invalid saved data and a stale revision before any file changes
        inspection = self.inspect()
        if inspection.state not in (PlanState.VALID, PlanState.MISSING):
            raise PlanRecordInvalid(inspection)
        if expected_revision != inspection.revision or isinstance(expected_revision, bool):
            raise RevisionConflict(
                f"expected plan revision {expected_revision}, current revision is {inspection.revision}")
        check_size(plan)
        # Keep the leftovers of an interrupted save as dated evidence, then publish atomically
        evidence = self._preserve(inspection.leftovers, moment)
        document = self._record.save({"plan": plan}, expected_revision)
        return document.revision, evidence

    def set_aside(self, moment: datetime) -> Path:
        """Rename invalid saved data to a dated name in the same folder, so a new plan can start at revision 0."""
        # Only invalid saved data is set aside; a valid plan changes only through a save
        inspection = self.inspect()
        if inspection.state in (PlanState.VALID, PlanState.MISSING):
            raise RepositoryError(f"the saved plan is {inspection.state.value}; there is nothing to set aside")
        target = self.folder / set_aside_name(moment)
        # Never replace earlier evidence; a second set aside in the same second waits for the next one
        if target.exists():
            raise RepositoryError("a set-aside plan with this time already exists; try again")
        replace_file(self.path, target)
        return target

    def leftovers(self) -> tuple[Path, ...]:
        """Return the leftover plan-save staging files; observers report none, because an owner may be saving."""
        if self.staging is not StagingPolicy.OWNER or not self.folder.is_dir():
            return ()
        return tuple(sorted(self.folder.glob(PLAN_STAGING_GLOB), key=lambda path: path.name))

    def changed_ns(self) -> int | None:
        """Return the modification time of the plan file in nanoseconds, or None when it is absent."""
        try:
            return os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            return None

    def _preserve(self, leftovers: tuple[Path, ...], moment: datetime) -> tuple[Path, ...]:
        """Rename each leftover staging file to the next free dated evidence name; never delete one."""
        moved: list[Path] = []
        number = 1
        for leftover in leftovers:
            # Skip numbers that earlier evidence of the same second already uses
            while (target := self.folder / interrupted_name(moment, number)).exists():
                number += 1
            replace_file(leftover, target)
            moved.append(target)
            number += 1
        return tuple(moved)


def check_size(plan: dict[str, Any]) -> None:
    """Refuse a plan whose canonical JSON is larger than the 8 MiB document limit."""
    if len(canonical_json(plan)) > MAX_DOCUMENT_BYTES:
        raise PlanValidationError((PlanProblem("the plan document is larger than 8 MiB"),))


def retired_marker(folder: Path) -> dict[str, str] | None:
    """Return the retired marker of an association folder, or None when the folder is not validly retired."""
    # Read only the published marker; a commit may stage the next one beside it
    inspection = VersionedJsonRepository(folder / RETIRED_FILE, 1, staging=StagingPolicy.OBSERVER).inspect()
    fields = dict(inspection.document.fields) if inspection.state is RecordState.VALID and inspection.document else {}
    # An unreadable or incomplete marker does not retire the folder, so the path-change state stays
    if set(fields) != RETIRED_FIELDS or not all(isinstance(value, str) for value in fields.values()):
        return None
    if (IDENTIFIER.fullmatch(fields["operation_id"]) is None or UTC_TIME.fullmatch(fields["retired_at"]) is None
            or TARGET_KEY.fullmatch(fields["new_mission_key"]) is None):
        return None
    return fields


def _mission_root(raw: object) -> str | None:
    """Return the mission root that a stored plan names, read leniently from a possibly invalid plan."""
    association = raw.get("association") if isinstance(raw, dict) else None
    root = association.get("mission_root") if isinstance(association, dict) else None
    return root if isinstance(root, str) and root else None
