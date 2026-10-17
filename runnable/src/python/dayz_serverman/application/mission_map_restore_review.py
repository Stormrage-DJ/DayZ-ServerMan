"""Review of Restore original and Restore previous state (leaf 8.8, R16, R17, A4, D2, D3, D5).

A restore writes the literal bytes and absences of plain records with digests.
It needs no task 6 seam and puts no current medical state on top (architecture
"Editor Records, Restore and Transport"):

- Restore original writes the writable files of the active protected
  original of the target. The family gate refuses it on an unverified family
  (6.11, Product Owner Q5). The review reads the gate from a fresh capability
  read of the target mission.
- Restore previous state writes the "files before" of the last-applied
  record, the R16 restore point. It never consults the family gate.

Neither writes a captured-only file, also when its current bytes differ from
the original (Architect 2026-10-14 12:16:06). A file that changed outside the
map editor (R17) is backed up before the restore overwrites it.

One fingerprint binds the review. It covers the mode, the profile and
settings revisions, the mission and the gate. It also covers the record
identities, the digests of the writable files and the medical baseline. The
review writes nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..adapters.windows.shared_files import read_bytes_shared
from ..domain.mission_map_applied import GROUPS_BEFORE_FIELD, GROUPS_FIELD
from ..domain.mission_map_families import family_gate
from ..domain.mission_map_plan import canonical_json
from ..domain.mission_map_restore_texts import (
    BACKUP_TEXT, DELETED_TEXT, INVALID_MODE, INVALID_MODE_TEXT, KEPT_CHANGES_TEXT, NO_ORIGINAL, NO_ORIGINAL_TEXT,
    NO_PREVIOUS_STATE, NO_PREVIOUS_STATE_TEXT, ORIGINAL, RECORD_KINDS, RESTORE_STALE_TEXT, RESTORED_COPY_TEXT,
    SHARED_ORIGINAL_TEXT, TARGET_UNPROJECTABLE, TARGET_UNPROJECTABLE_TEXT,
)
from ..domain.mission_map_terrain import TerrainCapabilities
from ..domain.profiles import ProfileRecord
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_contents import ContentStore
from ..repositories.mission_map_layout import MISSION_FILE_SET, TargetClass, association_folder
from ..repositories.mission_map_records import kept_originals
from ..repositories.mission_map_owned import OWNED_ERRORS, WRITABLE_MISSION_FILES, owned_digest
from ..repositories.mission_map_preview_base import (
    BASE_BASELINE, Base, PreviewRecords, build_base, file_changes, read_records,
)
from ..repositories.mission_map_records import OriginalStore
from ..repositories.mission_map_terrain import read_capabilities
from .mission_map_apply_choices import ApplyRefused
from .mission_map_plans import MissionMapPlanService, MissionPathChanged
from .mission_map_preview import PreviewStale
from .mission_map_preview_read import read_optional
from .mission_map_preview_targets import other_profiles

# Version of the restore answer and of its fingerprint basis
RESTORE_VERSION = 1


class ProfileList(Protocol):
    """The profile list that names the other profiles of the target (A4)."""

    def list(self) -> tuple[ProfileRecord, ...]:
        """Return every stored profile."""
        ...


class RestoreRefused(ApplyRefused):
    """Raised when a restore cannot start: an invalid mode or a refused review; nothing was written."""


@dataclass(frozen=True)
class RestoreContext:
    """What one restore review read and derived; the records of the restore commit come from it."""

    mode: str
    root: Path
    mission: Path
    association: dict[str, Any]
    profile_revision: int
    settings_revision: int
    terrain_id: str | None
    records: PreviewRecords
    base: Base
    # Every managed file now, and the promised bytes or absence of each writable file that the restore covers
    current: dict[str, bytes | None]
    target: dict[str, bytes | None]
    # Writable files whose target differs from the current file, and those of them with an external change
    changed: list[str]
    overwritten: list[str]


@dataclass(frozen=True)
class RestoreReview:
    """One complete restore review: the answer with its fingerprint and the context of the commit."""

    answer: dict[str, Any]
    fingerprint: str
    context: RestoreContext


class RestoreReader:
    """Build the review of a restore of the profile's active association; reads only, owner session (A11)."""

    def __init__(self, plans: MissionMapPlanService, profiles: ProfileList, *,
                 medical_baseline: Callable[[str, Path], Path | None],
                 capabilities: Callable[[Path], TerrainCapabilities] = read_capabilities) -> None:
        """Keep the plan service, the profile list, the medical baseline lookup (6.3) and the capability reader."""
        self._plans, self._profiles = plans, profiles
        self._medical_baseline, self.capabilities = medical_baseline, capabilities

    def revalidate(self, profile_id: object, mode: object, fingerprint: str) -> RestoreReview:
        """Review again and require the reviewed fingerprint; any relevant change raises PreviewStale."""
        review = self.review(profile_id, mode)
        if review.fingerprint != fingerprint:
            raise PreviewStale(RESTORE_STALE_TEXT)
        return review

    def review(self, profile_id: object, mode: object) -> RestoreReview:
        """Read the records and the mission and return the bound review of one restore mode."""
        if mode not in RECORD_KINDS:
            raise RestoreRefused(INVALID_MODE, INVALID_MODE_TEXT)
        area = self._plans._area
        profile, root, _relative, mission = self._plans._resolve(profile_id)
        settings_revision = self._plans._settings.load().revision
        loaded = self._plans.load(profile.values.profile_id)
        # In the mission path-change state the active records are not the admin's records (D2)
        if loaded["path_change"] is not None:
            raise MissionPathChanged(loaded["path_change"]["folders"], loaded["read_only_reason"])
        association = loaded["association"]
        pid, key = association["profile_id"], association["mission_key"]
        records = read_records(area, pid, key)
        current = {path: read_optional(mission, path) for path in MISSION_FILE_SET}
        refusals: list[dict[str, Any]] = []
        gate, terrain_id = None, None
        if mode == ORIGINAL:
            # A fresh capability read of the target mission gives the family gate (6.11)
            terrain_id = self.capabilities(mission).terrain_id
            gate = family_gate(terrain_id)
            target = self._original(area, key, records, refusals)
            if not gate.verified:
                refusals.append(_refusal(gate.reason, gate.text))
        else:
            target = self._previous(area, pid, key, records, refusals)
        changed = [path for path in WRITABLE_MISSION_FILES if path in target and target[path] != current[path]]
        # A written state without an owned-value projection is never offered (Architect 07:07:28, A88-F2)
        refusals += [_refusal(TARGET_UNPROJECTABLE, TARGET_UNPROJECTABLE_TEXT.format(path=path), path)
                     for path in changed if target[path] is not None and not _projected(path, target[path])]
        baseline_path = self._medical_baseline(pid, mission)
        medical = None if baseline_path is None else read_bytes_shared(baseline_path)
        base = build_base(area, pid, key, records, current)
        external, coordinated = file_changes(current, base, medical)
        overwritten = [item["path"] for item in external if item["path"] in changed]
        others = other_profiles(area, root, self._profiles.list(), pid, key)
        applied = records.applied_record
        answer = {
            "version": RESTORE_VERSION, "mode": mode, "operation_kind": RECORD_KINDS[mode],
            "profile": {"profile_id": pid, "display_name": profile.values.display_name, "revision": profile.revision},
            "settings_revision": settings_revision,
            "mission": {"mission_name": mission.name, "mission_root": association["mission_root"], "mission_key": key},
            "family": None if gate is None else gate.to_dict(),
            "refused": bool(refusals), "refusals": refusals,
            "original": _original_answer(records, others, base, _restored_copy(area, pid, key)) if mode == ORIGINAL else None,
            "replaces": None if applied is None else {name: applied[name] for name in (
                "operation_id", "operation_kind", "applied_at")},
            "affected_files": [_affected(path, current[path], target[path]) for path in changed],
            "external_changes": [dict(item, overwritten=item["path"] in changed, backup=item["path"] in overwritten,
                                      text=_backup_text(item) if item["path"] in overwritten else None)
                                 for item in external],
            "coordinated_changes": [dict(item, overwritten=item["path"] in changed) for item in coordinated],
            "realization": {"generated_groups": [] if mode == ORIGINAL else (
                None if applied is None else applied.get(GROUPS_BEFORE_FIELD)),
                "generated_groups_before": None if applied is None else applied.get(GROUPS_FIELD)},
            "other_profiles": others,
            "inputs": {"files": {path: _digest(current[path]) for path in WRITABLE_MISSION_FILES},
                       "records": records.identities(), "medical_baseline": _digest(medical)},
        }
        fingerprint = hashlib.sha256(canonical_json({"version": RESTORE_VERSION, "dayz_root": str(root),
                                                     "answer": answer})).hexdigest()
        context = RestoreContext(mode, root, mission, association, profile.revision, settings_revision, terrain_id,
                                 records, base, current, target, changed, overwritten)
        return RestoreReview(dict(answer, fingerprint=fingerprint), fingerprint, context)

    @staticmethod
    def _original(area: Path, key: str, records: PreviewRecords,
                  refusals: list[dict[str, Any]]) -> dict[str, bytes | None]:
        """Return the writable files of the active original of the target; captured-only files never."""
        if records.original is None:
            refusals.append(_refusal(NO_ORIGINAL, NO_ORIGINAL_TEXT))
            return {}
        contents = OriginalStore(area, key, staging=StagingPolicy.OBSERVER).contents
        return {item["path"]: contents.read(item["sha256"]) if item["existed"] else None
                for item in records.original["files"] if item["path"] in WRITABLE_MISSION_FILES}

    @staticmethod
    def _previous(area: Path, profile_id: str, key: str, records: PreviewRecords,
                  refusals: list[dict[str, Any]]) -> dict[str, bytes | None]:
        """Return the "files before" of the last-applied record: the R16 restore point of this association."""
        if records.applied is None or records.baseline is None:
            refusals.append(_refusal(NO_PREVIOUS_STATE, NO_PREVIOUS_STATE_TEXT))
            return {}
        contents = ContentStore(association_folder(area, profile_id, key))
        return {item["path"]: contents.read(item["sha256"]) if item["existed"] else None
                for item in records.applied[0]["files_before"]
                if item["target_key"] == key and item["path"] in WRITABLE_MISSION_FILES}


def _restored_copy(area: Path, profile_id: str, key: str) -> bool:
    """Return whether the association folder keeps an inactive restored copy of the original of this folder."""
    return any(item["target_key"] == key for item in kept_originals(association_folder(area, profile_id, key)))


def _original_answer(records: PreviewRecords, others: list[dict[str, Any]], base: Base,
                     restored_copy: bool = False) -> dict[str, Any] | None:
    """Return the active original that Restore original writes; on a shared target the preview says so (D5).

    A restored copy of a different original adds its sentence once after the shared text; Restore original never
    uses that copy.

    `kept_changes` is the Product Owner sentence when the working-baseline files differ from the writable
    files of the active original. Today only Keep the change makes them differ (ruling 1a).
    """
    original = records.original
    if original is None:
        return None
    shared = bool(others)
    files = {item["path"]: (item["existed"], item["sha256"]) for item in original["files"]}
    kept = base.source == BASE_BASELINE and any(
        (data is not None, _digest(data)) != files.get(path) for path, data in base.files.items()
        if path in WRITABLE_MISSION_FILES)
    return {"target_key": original["target_key"], "manifest_sha256": original["manifest_sha256"],
            "captured_at": original["captured_at"], "profile_id": original["profile_id"],
            "operation_id": original["operation_id"], "active": True, "shared": shared,
            "text": (f"{SHARED_ORIGINAL_TEXT} {RESTORED_COPY_TEXT}" if restored_copy else SHARED_ORIGINAL_TEXT)
                    if shared else None,
            "kept_changes": KEPT_CHANGES_TEXT if kept else None}


def _backup_text(change: dict[str, Any]) -> str:
    """Return the preview line of an overwritten external change: a copy, or a deleted file written again."""
    return (BACKUP_TEXT if change["exists_now"] else DELETED_TEXT).format(path=change["path"])


def _projected(path: str, data: bytes) -> bool:
    """Return whether the owned values of present bytes can be read; a damaged owned block cannot."""
    try:
        return owned_digest(TargetClass.MISSION, path, data) is not None
    except OWNED_ERRORS:
        return False


def _affected(path: str, before: bytes | None, after: bytes | None) -> dict[str, Any]:
    """Return one file that the restore writes, with existence and digests before and after."""
    return {"path": path, "target_class": TargetClass.MISSION.value, "exists_before": before is not None,
            "exists_after": after is not None, "sha256_before": _digest(before), "sha256_after": _digest(after)}


def _refusal(reason: str | None, message: str | None, file: str | None = None) -> dict[str, Any]:
    """Return one refusal entry in the shape of the apply preview refusals (8.6)."""
    return {"reason": reason, "file": file, "source": None, "field": None, "placements": [], "message": message,
            "object_id": None}


def _digest(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()
