"""Load, save and set aside the mission map plan of a profile's active mission association (D2, R15, R20)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..domain.mission_map_plan import PlanForm, parse_plan
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_layout import (
    TargetClass, association_folder, is_target_key, ordinal_spelling, profile_folder, target_key,
)
from ..repositories.mission_map_plans import MissionMapPlanStore, PlanInspection, PlanState, retired_marker
from ..repositories.mission_map_records import record_mission_root
from .mission_configuration import _contained, resolve_profile_mission
from .mission_map_locks import AssociationLocks
from .profiles import ProfileService
from .settings import SettingsService, SettingsValidationError


class MissionPathChanged(RuntimeError):
    """Raised when the profile's mission moved: the old plan is read only until task 10 adoption."""

    def __init__(self, folders: list[dict[str, Any]], reason: str = "mission_path_changed") -> None:
        """Store every matching old association folder."""
        self.folders = folders
        self.reason = reason
        super().__init__("the mission path of this profile changed; adopt the earlier plan before saving")


class ObserverWriteRefused(PermissionError):
    """Raised when an observer session asks for a plan write; other PermissionErrors are storage failures."""


class AssociationMismatch(ValueError):
    """Raised when a plan names another profile, mission or runtime profile than the active association."""
    pass


class MissionMapPlanService:
    """Own the plan record of each profile and mission association; plan saves never change mission files."""

    def __init__(
        self, profiles: ProfileService, settings: SettingsService, area: Path,
        locks: AssociationLocks | None = None, *, staging: StagingPolicy = StagingPolicy.OWNER,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Store collaborators; the area is PortablePaths.mission_map, and observers pass the observer policy."""
        self._profiles = profiles
        self._settings = settings
        self._area = area
        self._locks = locks or AssociationLocks()
        self._staging = staging
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def active_association(self, profile_id: object) -> dict[str, Any]:
        """Return the association of the profile's current mission; its plan starts without a runtime key."""
        profile, _root, mission_root, mission = self._resolve(profile_id)
        return {"profile_id": profile.values.profile_id, "mission_root": mission_root,
                "mission_key": target_key(TargetClass.MISSION, mission), "runtime_profile_key": None}

    def load(self, profile_id: object) -> dict[str, Any]:
        """Return the active plan, or in the path-change state the newest matching old plan as read only."""
        # Resolve the current mission and look for old folders of the same mission root
        association = self.active_association(profile_id)
        matches = self._path_change(association)
        # In the path-change state the active folder is not read or created; the admin sees the real plan
        if matches:
            inspection = plan_store(self._area, association["profile_id"], matches[0]["mission_key"],
                                    self._staging).inspect()
            change = {"shown_mission_key": matches[0]["mission_key"], "folders": matches}
            reason = self._change_reason(association)
            return _load_result(association, inspection, read_only=True, path_change=change, reason=reason)
        # Otherwise report the active record as it is, valid, missing or invalid
        inspection = plan_store(self._area, association["profile_id"], association["mission_key"],
                                self._staging).inspect()
        return _load_result(association, inspection, read_only=False, path_change=None)

    def save(self, profile_id: object, raw_plan: object, expected_revision: object, *,
             before_save: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Save the active plan under its lock and revision guard; before_save validates locked record identities."""
        self._require_owner()
        # Validate the revision proof and the whole document before any filesystem work
        if expected_revision is not None and (
                isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0):
            raise ValueError("expected_revision must be null or a non-negative integer")
        plan = parse_plan(raw_plan, PlanForm.STORED)
        # Prove that the plan belongs to the profile's current mission
        profile, root, mission_root, mission = self._resolve(profile_id)
        association = {"profile_id": profile.values.profile_id, "mission_root": mission_root,
                       "mission_key": target_key(TargetClass.MISSION, mission)}
        _check_association(plan["association"], association, lambda: _runtime_key(root, profile))
        # The association lock is the only lock of a plan save; the path-change check repeats under it
        with self._locks.hold(association["profile_id"], association["mission_key"]):
            matches = self._path_change(association)
            if matches:
                raise MissionPathChanged(matches, self._change_reason(association))
            # A coordinator checks record identities here, after any commit releases the same lock
            if before_save is not None:
                before_save(plan)
            store = plan_store(self._area, association["profile_id"], association["mission_key"])
            revision, evidence = store.save(plan, expected_revision, self._clock())
        return {"association": plan["association"], "revision": revision, "plan": plan,
                "interrupted_evidence": [path.name for path in evidence]}

    def set_aside(self, profile_id: object, mission_key: object = None) -> dict[str, Any]:
        """Rename invalid saved data of the active association to a dated name; a new plan starts at revision 0.

        A given mission key must name the active association, so a stale editor cannot set aside another plan.
        """
        self._require_owner()
        association = self.active_association(profile_id)
        if mission_key is not None and mission_key != association["mission_key"]:
            raise AssociationMismatch("The map editor names another mission. Reload the plan.")
        with self._locks.hold(association["profile_id"], association["mission_key"]):
            # In the path-change state the records of an existing active folder stay unchanged
            matches = self._path_change(association)
            if matches:
                raise MissionPathChanged(matches, self._change_reason(association))
            store = plan_store(self._area, association["profile_id"], association["mission_key"])
            target = store.set_aside(self._clock())
        return {"association": association, "set_aside": target.name}

    def _path_change(self, association: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the old association folders of the same mission root, newest plan first, then by key."""
        folder = profile_folder(self._area, association["profile_id"])
        if not folder.is_dir():
            return []
        # A return to a retired active key follows the adoption chain instead of reviving its records
        active = association_folder(self._area, association["profile_id"], association["mission_key"])
        if retired_marker(active) is not None:
            return _retired_successors(self._area, association, self._staging)
        wanted = _root_spelling(association["mission_root"])
        matches = []
        for child in folder.iterdir():
            # Only other, unretired association folders take part
            if not child.is_dir() or not is_target_key(child.name) or child.name == association["mission_key"]:
                continue
            if retired_marker(child) is not None:
                continue
            # Compare the mission root that the folder's plan names, also when that plan is invalid;
            # a folder without a readable plan falls back to its last-applied or baseline record
            store = plan_store(self._area, association["profile_id"], child.name, StagingPolicy.OBSERVER)
            root = store.inspect().mission_root or record_mission_root(child)
            if root is not None and _root_spelling(root) == wanted:
                matches.append({"mission_key": child.name, "mission_root": root, "plan_changed_ns": store.changed_ns()})
        # Show the folder whose plan file changed last; equal times are ordered by mission key
        matches.sort(key=lambda match: (-(match["plan_changed_ns"] or 0), match["mission_key"]))
        return matches

    def _change_reason(self, association: dict[str, Any]) -> str:
        """Distinguish a moved mission from a return to its own retired association."""
        folder = association_folder(self._area, association["profile_id"], association["mission_key"])
        return "returned_to_earlier_path" if retired_marker(folder) is not None else "mission_path_changed"

    def _resolve(self, profile_id: object) -> tuple[Any, Path, str, Path]:
        """Return the profile, the DayZ root, the relative mission root and the resolved mission folder."""
        # Read the profile and the configured DayZ root, then resolve the mission inside it
        profile = self._profiles.read(profile_id)
        settings = self._settings.load()
        if settings.dayz_root is None:
            raise SettingsValidationError("DayZ root is not configured")
        root = Path(settings.dayz_root).resolve(strict=True)
        mission_root, mission = resolve_profile_mission(root, profile)
        return profile, root, mission_root, mission

    def _require_owner(self) -> None:
        """Refuse a write in an observer session."""
        if self._staging is not StagingPolicy.OWNER:
            raise ObserverWriteRefused("observer sessions only read mission map plans")


def plan_store(area: Path, profile_id: str, mission_key: str,
               staging: StagingPolicy = StagingPolicy.OWNER) -> MissionMapPlanStore:
    """Return the plan store of one association folder, bound to that association for the D2 classification."""
    return MissionMapPlanStore(association_folder(area, profile_id, mission_key), staging=staging,
                               association=(profile_id, mission_key))


def _check_association(stored: dict[str, Any], active: dict[str, Any], runtime: Callable[[], str]) -> None:
    """Refuse a plan whose association is not the active one; a runtime key must match the profile's folder."""
    if stored["profile_id"] != active["profile_id"] or stored["mission_key"] != active["mission_key"]:
        raise AssociationMismatch("the plan belongs to another profile or mission")
    if _root_spelling(stored["mission_root"]) != _root_spelling(active["mission_root"]):
        raise AssociationMismatch("the plan names another mission root")
    if stored["runtime_profile_key"] is not None and stored["runtime_profile_key"] != runtime():
        raise AssociationMismatch("the plan names another runtime profile folder")


def _runtime_key(root: Path, profile: Any) -> str:
    """Return the target key of the profile's runtime profile folder after the containment checks."""
    relative = profile.values.runtime_profile
    if relative is None:
        raise AssociationMismatch("the profile has no runtime profile folder")
    return target_key(TargetClass.RUNTIME, _contained(root, root / relative, directory=True))


def _root_spelling(mission_root: str) -> str | None:
    """Return the comparison form of a relative mission root, by the same rule as the target keys.

    A root that holds a lone surrogate has no form, so it matches no root. The active root never holds one,
    because its target key refuses it first; a leniently read old record may.
    """
    try:
        return ordinal_spelling(mission_root)
    except ValueError:
        return None


def _load_result(association: dict[str, Any], inspection: PlanInspection, *, read_only: bool,
                 path_change: dict[str, Any] | None, reason: str | None = None) -> dict[str, Any]:
    """Return the load answer: state, revision, plan, invalid-data detail and the path-change report."""
    return {
        "association": association, "state": inspection.state.value, "revision": inspection.revision,
        # Only a valid plan is shown; another association's plan stays evidence in its file
        "plan": inspection.plan if inspection.state is PlanState.VALID else None, "detail": inspection.detail, "read_only": read_only,
        "interrupted_evidence": [path.name for path in inspection.leftovers], "path_change": path_change,
        "read_only_reason": reason,
        "problems": [{"object_id": problem.object_id, "rule": "invalid_document", "message": problem.message}
                     for problem in inspection.problems],
    }


def _retired_successors(area: Path, association: dict[str, Any], staging: StagingPolicy) -> list[dict[str, Any]]:
    """Follow adoption markers to the newest unretired folder; refuse a broken or cyclic chain."""
    key = association["mission_key"]
    seen: set[str] = set()
    successors: list[dict[str, Any]] = []
    while True:
        # Never use a retired plan as active, or loop indefinitely through damaged adoption evidence
        if key in seen:
            raise MissionPathChanged(successors, "returned_to_earlier_path")
        seen.add(key)
        folder = association_folder(area, association["profile_id"], key)
        if not folder.is_dir():
            raise MissionPathChanged(successors, "returned_to_earlier_path")
        store = plan_store(area, association["profile_id"], key, staging)
        marker = retired_marker(folder)
        if key != association["mission_key"]:
            root = store.inspect().mission_root or record_mission_root(folder) or association["mission_root"]
            successors.insert(0, {"mission_key": key, "mission_root": root, "plan_changed_ns": store.changed_ns()})
        if marker is None:
            return successors
        key = marker["new_mission_key"]
