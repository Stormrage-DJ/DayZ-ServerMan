"""The journal, the records and the candidate bytes of one Apply commit (8.7, D3, D4).

Every value comes from the reviewed preview, the choices and the bytes that
the review read. The commit step (8.4) checks the links again before staging.

- Journal groups: one for each writable file whose candidate differs from the
  current file. A captured-only file is never a group, a ledger file item or a
  "files before" or "files after" item (Architect 2026-10-14 12:16:06).
- Original: captured from the current files at the first Apply on the target.
- Working baseline and restore point: `mission_map_apply_history.py`.
- Last applied (version 2): "files after" lists every writable file. The
  record also stores the global value, the plan objects, the realization of
  the generated groups and the realization of its "files before" state.
- Ledger entry: one for the mission target when at least one file changes.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..domain.mission_map_applied import GLOBAL_FIELD, GROUPS_BEFORE_FIELD, GROUPS_FIELD, OBJECTS_FIELD
from ..domain.mission_map_plan import configuration_fingerprint
from ..domain.mission_map_records import sealed
from ..repositories.mission_map_commit_records import RecordUpdate
from ..repositories.mission_map_journal import MapGroup, MapJournal
from ..repositories.mission_map_journal_paths import group_staging_path, recovery_copy_path, target_folder
from ..repositories.mission_map_journal_store import recovery_folder
from ..repositories.mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, MISSION_FILE_SET, TargetClass, association_folder, ledger_folder, original_folder,
)
from ..repositories.mission_map_mapgroup_candidate import CloneRealization
from ..repositories.mission_map_owned import OWNED_ERRORS, WRITABLE_MISSION_FILES, owned_digest
from ..repositories.mission_map_preview_candidates import PreviewCandidates
from ..repositories.mission_map_records import ORIGINAL_FILE
from ..repositories.paths import PortablePaths
from ..repositories.restore_paths import missing_ancestors
from .mission_map_apply_choices import Choices
from .mission_map_apply_history import contents_of, restore_point, sha, stored, working_baseline
from .mission_map_preview import PreviewReview

# Applied-record fields that an unchanged reapply keeps; the operation, time and "files before" may differ
SAME_APPLY_FIELDS = ("mission_root", "operation_kind", "plan_id", "plan_revision", "configuration_fingerprint",
                     "baseline_revision", "baseline_manifest_sha256", "files_after", "excluded_encounters",
                     GLOBAL_FIELD, OBJECTS_FIELD, GROUPS_FIELD, GROUPS_BEFORE_FIELD)


@dataclass(frozen=True)
class Publication:
    """One Apply commit: the PREPARED journal, the record candidates and the DayZ-side candidate bytes."""

    journal: MapJournal
    updates: list[RecordUpdate]
    candidates: dict[tuple[str, str], bytes]
    unchanged: bool
    # Each file whose external version a Replace overwrote, with the digest of the kept backup
    replaced: list[dict[str, Any]]


def build_publication(review: PreviewReview, choices: Choices, candidates: PreviewCandidates,
                      base: dict[str, bytes | None], *, operation_id: str, now: str,
                      paths: PortablePaths) -> Publication:
    """Return the commit of one Apply; `unchanged` is true when the commit would change nothing."""
    context = review.context
    assert context is not None
    association, plan = context.association, context.plan
    pid, key, mission_root = association["profile_id"], association["mission_key"], association["mission_root"]
    current, contents = review.inputs.managed, candidates.contents()
    after = {path: contents[path] for path in WRITABLE_MISSION_FILES}
    changed = [path for path in WRITABLE_MISSION_FILES if after[path] != current[path]]
    folder = target_folder(context.root, mission_root)
    groups = [_group(paths, folder, context.root, operation_id, path, current[path], after[path]) for path in changed]
    journal = MapJournal(operation_id, "apply", pid, mission_root, key, None, None, str(context.root),
                         plan["plan_id"], context.plan_revision, configuration_fingerprint(plan),
                         review.fingerprint, context.profile_revision, context.settings_revision, groups, [])
    area = paths.mission_map
    association_path = association_folder(area, pid, key)
    updates: list[RecordUpdate] = []
    # The first Apply on the target captures every managed file, also absent ones (D2)
    original = context.records.original
    if original is None:
        original = sealed({"target_class": "mission", "target_key": key, "display_path": str(context.mission),
                           "mission_root": mission_root, "terrain_id": context.terrain_id, "captured_at": now,
                           "profile_id": pid, "operation_id": operation_id,
                           "files": [stored(path, current[path]) for path in MISSION_FILE_SET]})
        updates.append(RecordUpdate("original", paths.relative(original_folder(area, key) / ORIGINAL_FILE),
                                    original, None, contents_of(current.values())))
    # A Replace backup only for a file that a group overwrites (R17)
    replaced = [path for path in choices.replaced if path in changed]
    baseline, revision, expected, base_contents = working_baseline(
        context, original, base, choices, replaced, current, operation_id, now, area)
    updates.append(RecordUpdate("baseline", paths.relative(association_path / BASELINE_FILE), baseline, expected,
                                base_contents))
    files_after = [{"target_key": key, "path": path, "existed": data is not None, "sha256": sha(data),
                    "owned_sha256": _owned(path, data)} for path, data in after.items()]
    files_before, before_contents, groups_before = restore_point(context, changed, files_after, current, area)
    applied = sealed({
        "profile_id": pid, "mission_key": key, "mission_root": mission_root, "operation_id": operation_id,
        "operation_kind": "apply", "applied_at": now, "plan_id": plan["plan_id"],
        "plan_revision": context.plan_revision, "configuration_fingerprint": configuration_fingerprint(plan),
        "baseline_revision": revision, "baseline_manifest_sha256": baseline["manifest_sha256"],
        "files_after": files_after, "files_before": files_before, "excluded_encounters": list(choices.excluded),
        GLOBAL_FIELD: plan["global_abundance"], OBJECTS_FIELD: copy.deepcopy(plan["objects"]),
        GROUPS_FIELD: [realization(clone) for clone in candidates.mapgroups.clones],
        GROUPS_BEFORE_FIELD: groups_before})
    found = context.records.applied
    updates.append(RecordUpdate("applied", paths.relative(association_path / APPLIED_FILE), applied,
                                None if found is None else found[1], before_contents))
    if changed:
        entry = sealed({
            "operation_id": operation_id, "operation_kind": "apply", "recorded_at": now, "profile_id": pid,
            "target_class": "mission", "target_key": key, "display_path": str(context.mission),
            "mission_root": mission_root, "output": True, "carried_operation_id": None,
            "files": [_ledger_file(path, current[path], after[path], base[path]) for path in changed]})
        updates.append(RecordUpdate("ledger", paths.relative(ledger_folder(area, key) / f"{operation_id}.json"),
                                    entry))
    unchanged = not changed and expected == revision and found is not None and all(
        found[0].get(name) == applied[name] for name in SAME_APPLY_FIELDS)
    candidate_bytes = {("mission", path): data for path in changed if (data := after[path]) is not None}
    return Publication(journal, updates, candidate_bytes, unchanged,
                       [{"path": path, "sha256": sha(current[path])} for path in replaced])


def realization(clone: CloneRealization) -> dict[str, Any]:
    """Return the plain record form of one generated group (D3 "Native Realization")."""
    return {"name": clone.name, "key": copy.deepcopy(clone.key), "source": list(clone.source),
            "containers": list(clone.containers), "group_ceiling": clone.group_ceiling,
            "container_ceilings": list(clone.container_ceilings),
            "detached": [{"file": item.file, "name": item.name, "position": list(item.position),
                          "rotation": None if item.rotation is None else list(item.rotation), "angle": item.angle,
                          "multiplicity": item.multiplicity} for item in clone.detached]}


def _group(paths: PortablePaths, folder: Path, root: Path, operation_id: str, relative: str,
           old: bytes | None, new: bytes | None) -> MapGroup:
    """Return the journal group of one changed file, with the derived staging and recovery paths (D4)."""
    target = folder.joinpath(*PurePosixPath(relative).parts)
    ancestors = missing_ancestors(root, target) if new is not None else ()
    recovery = recovery_copy_path(recovery_folder(paths).resolve(), operation_id, "mission", relative)
    return MapGroup("mission", relative, str(target), str(group_staging_path(target, operation_id, ancestors)),
                    str(recovery) if old is not None else None, old is not None, sha(old), new is not None, sha(new),
                    created_ancestors=tuple(str(path) for path in ancestors))


def _ledger_file(path: str, before: bytes | None, after: bytes | None, base: bytes | None) -> dict[str, Any]:
    """Return one changed-file item of the ledger entry: whole and owned digests before, after and of the base."""
    return {"path": path, "before_sha256": sha(before), "after_sha256": sha(after), "baseline_sha256": sha(base),
            "owned_before_sha256": _owned(path, before), "owned_after_sha256": _owned(path, after),
            "owned_baseline_sha256": _owned(path, base)}


def owned_or_none(target_class: TargetClass, path: str, data: bytes | None) -> str | None:
    """Return the owned-value digest, or None when the owned block of the file is damaged."""
    try:
        return owned_digest(target_class, path, data)
    except OWNED_ERRORS:
        return None


def _owned(path: str, data: bytes | None) -> str | None:
    """Return the owned-value digest of a mission file, or None for a damaged block."""
    return owned_or_none(TargetClass.MISSION, path, data)
