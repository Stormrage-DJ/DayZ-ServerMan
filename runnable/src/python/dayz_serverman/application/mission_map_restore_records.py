"""The journal, the records and the candidate bytes of one restore commit (leaf 8.8, R16, R17, D2, D3, D4).

Every value comes from the restore review. The commit step (8.4) checks the
links again before staging.

- Journal groups: one for each writable file whose promised bytes or absence
  differ from the current file. A captured-only file is never a group.
- Working baseline: its files do not change. The restore can overwrite a file
  that changed outside the map editor. That version is kept as a `replaced`
  provenance entry with its content (R17, Architect 2026-10-15 01:18:47).
  Restore original keeps the provenance list and adds its own entry. Without
  a baseline, Restore original creates it from the original, as the first
  Apply does (D3).
- Last applied (version 2, kind `restore_original` or `restore_previous`):
  the plan fields are null. `generated_groups` is the realization of the
  bytes that the restore writes. It is `[]` for Restore original. For Restore
  previous state it is the `generated_groups_before` of the current record,
  which can be null. `generated_groups_before` is the `generated_groups` of
  the current record.
- Files of the restore record: "Files before" lists the groups with their current
  content. So a later Restore previous state can return to the state before
  the restore. "Files after" lists every writable file. A file that the
  restore covers has its promised state. Each other file keeps its earlier
  reference, so that an external change of it stays visible to the next preview.
- Ledger entry: one for the mission target when a file changes, with
  `output` false (D3), so the capture check never matches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..domain.mission_map_applied import (
    GLOBAL_FIELD, GROUPS_BEFORE_FIELD, GROUPS_FIELD, OBJECTS_FIELD, REPLACED_FILES, REPLACED_SOURCE,
)
from ..domain.mission_map_records import MAX_PROVENANCE, sealed
from ..domain.mission_map_restore_texts import ORIGINAL, PREVIOUS, RECORD_KINDS
from ..repositories.mission_map_commit_records import RecordUpdate
from ..repositories.mission_map_contents import ContentStore
from ..repositories.mission_map_journal import MapGroup, MapJournal
from ..repositories.mission_map_journal_paths import group_staging_path, recovery_copy_path, target_folder
from ..repositories.mission_map_journal_store import recovery_folder
from ..repositories.mission_map_layout import (
    APPLIED_FILE, BASELINE_FILE, MISSION_FILE_SET, TargetClass, association_folder, ledger_folder,
)
from ..repositories.mission_map_owned import WRITABLE_MISSION_FILES
from ..repositories.paths import PortablePaths
from ..repositories.restore_paths import missing_ancestors
from .mission_map_apply_history import contents_of, sha, stored
from .mission_map_apply_records import owned_or_none
from .mission_map_restore_review import RestoreContext, RestoreReview


@dataclass(frozen=True)
class RestorePublication:
    """One restore commit: the PREPARED journal, the record candidates and the DayZ-side candidate bytes."""

    journal: MapJournal
    updates: list[RecordUpdate]
    candidates: dict[tuple[str, str], bytes]
    unchanged: bool
    # Each overwritten file that changed outside the map editor, with the digest of the kept copy
    backups: list[dict[str, Any]]


def build_restore(review: RestoreReview, *, operation_id: str, now: str, paths: PortablePaths) -> RestorePublication:
    """Return the commit of one restore; `unchanged` is true when the restore would change nothing."""
    context = review.context
    kind = RECORD_KINDS[context.mode]
    association = context.association
    pid, key, mission_root = association["profile_id"], association["mission_key"], association["mission_root"]
    current, target, changed = context.current, context.target, context.changed
    folder = target_folder(context.root, mission_root)
    groups = [_group(paths, folder, context.root, operation_id, path, current[path], target[path]) for path in changed]
    # A restore has no plan identity and no preview fingerprint in its journal (D4)
    journal = MapJournal(operation_id, kind, pid, mission_root, key, None, None, str(context.root), None, None, None,
                         None, context.profile_revision, context.settings_revision, groups, [])
    area = paths.mission_map
    association_path = association_folder(area, pid, key)
    baseline, revision, expected, base_contents = _baseline(context, operation_id, now, area)
    updates = [RecordUpdate("baseline", paths.relative(association_path / BASELINE_FILE), baseline, expected,
                            base_contents)]
    found = context.records.applied
    previous = None if found is None else found[0]
    files_after = _files_after(context, key, previous)
    applied = sealed({
        "profile_id": pid, "mission_key": key, "mission_root": mission_root, "operation_id": operation_id,
        "operation_kind": kind, "applied_at": now, "plan_id": None, "plan_revision": None,
        "configuration_fingerprint": None, "baseline_revision": revision,
        "baseline_manifest_sha256": baseline["manifest_sha256"], "files_after": files_after,
        "files_before": [dict(stored(path, current[path]), target_key=key) for path in changed],
        "excluded_encounters": [], GLOBAL_FIELD: None, OBJECTS_FIELD: None,
        # The realization of the bytes that the restore writes, and of the state that it replaces (Architect 01:18:47)
        GROUPS_FIELD: [] if context.mode == ORIGINAL else (None if previous is None else previous.get(GROUPS_BEFORE_FIELD)),
        GROUPS_BEFORE_FIELD: None if previous is None else previous.get(GROUPS_FIELD)})
    updates.append(RecordUpdate("applied", paths.relative(association_path / APPLIED_FILE), applied,
                                None if found is None else found[1], contents_of(current[path] for path in changed)))
    if changed:
        base = context.base.files
        entry = sealed({
            "operation_id": operation_id, "operation_kind": kind, "recorded_at": now, "profile_id": pid,
            "target_class": "mission", "target_key": key, "display_path": str(context.mission),
            "mission_root": mission_root, "output": False, "carried_operation_id": None,
            "files": [_ledger_file(path, current[path], target[path], base[path]) for path in changed]})
        updates.append(RecordUpdate("ledger", paths.relative(ledger_folder(area, key) / f"{operation_id}.json"),
                                    entry))
    # Nothing to write: no file differs, and Restore original already holds its own record over these files
    unchanged = not changed and (context.mode == PREVIOUS or previous is None or (
        previous["operation_kind"] == kind and previous["files_after"] == files_after))
    candidates = {("mission", path): data for path in changed if (data := target[path]) is not None}
    return RestorePublication(journal, updates, candidates, unchanged,
                              [{"path": path, "sha256": sha(current[path])} for path in context.overwritten])


def _baseline(context: RestoreContext, operation_id: str, now: str,
              area: Path) -> tuple[dict[str, Any], int, int | None, dict[str, bytes]]:
    """Return the baseline record, its revision after the commit, the revision read before it and its content."""
    key = context.association["mission_key"]
    entries = []
    if context.overwritten:
        # The external versions that the restore overwrites are kept with their content (R17)
        entries.append({"time": now, "source": REPLACED_SOURCE, "operation_id": operation_id,
                        "paths": list(context.overwritten),
                        REPLACED_FILES: [dict(stored(path, context.current[path]), target_key=key)
                                         for path in context.overwritten]})
    if context.mode == ORIGINAL:
        entries.append({"time": now, "source": "restore_original", "operation_id": operation_id,
                        "paths": list(WRITABLE_MISSION_FILES)})
    # The base is the baseline, or the original when the association has no baseline yet (8.6 base rule)
    contents = contents_of(context.base.files.values())
    contents.update(contents_of(context.current[path] for path in context.overwritten))
    found = context.records.baseline
    if found is None:
        original = context.records.original
        assert original is not None
        files = [dict(stored(path, context.base.files[path]), target_key=key) for path in MISSION_FILE_SET]
        provenance = [{"time": now, "source": "original", "operation_id": operation_id,
                       "paths": list(MISSION_FILE_SET)}] + entries
        record = {"profile_id": context.association["profile_id"], "mission_key": key,
                  "mission_root": context.association["mission_root"], "target_keys": [key],
                  "originals": [{"target_key": key, "manifest_sha256": original["manifest_sha256"]}],
                  "files": files, "provenance": provenance[-MAX_PROVENANCE:]}
        return sealed(record), 0, None, contents
    record, revision = found
    # The content of earlier Replace backups stays referenced by the new record
    store = ContentStore(association_folder(area, context.association["profile_id"], key))
    for item in record["provenance"]:
        contents.update({stored_file["sha256"]: store.read(stored_file["sha256"])
                         for stored_file in item.get(REPLACED_FILES, []) if stored_file["existed"]})
    if not entries:
        return record, revision, revision, contents
    content = {name: value for name, value in record.items() if name != "manifest_sha256"}
    provenance = (record["provenance"] + entries)[-MAX_PROVENANCE:]
    return sealed(dict(content, provenance=provenance)), revision + 1, revision, contents


def _files_after(context: RestoreContext, key: str, previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return "files after" of every writable file: the promised state, else the earlier reference (R17)."""
    kept = {} if previous is None else {
        item["path"]: item for item in previous["files_after"] if item["target_key"] == key}
    after = []
    for path in WRITABLE_MISSION_FILES:
        if path not in context.target and path in kept:
            after.append(kept[path])
            continue
        data = context.target[path] if path in context.target else context.current[path]
        after.append({"target_key": key, "path": path, "existed": data is not None, "sha256": sha(data),
                      "owned_sha256": owned_or_none(TargetClass.MISSION, path, data)})
    return after


def _group(paths: PortablePaths, folder: Path, root: Path, operation_id: str, relative: str,
           old: bytes | None, new: bytes | None) -> MapGroup:
    """Return the journal group of one restored file, with the derived staging and recovery paths (D4).

    The paths follow the same derivation as an Apply group; journal path safety derives them again.
    A restored absence has no new digest, and a restored file in a missing folder creates its ancestors.
    """
    target = folder.joinpath(*PurePosixPath(relative).parts)
    ancestors = missing_ancestors(root, target) if new is not None else ()
    recovery = recovery_copy_path(recovery_folder(paths).resolve(), operation_id, "mission", relative)
    return MapGroup("mission", relative, str(target), str(group_staging_path(target, operation_id, ancestors)),
                    str(recovery) if old is not None else None, old is not None, sha(old), new is not None, sha(new),
                    created_ancestors=tuple(str(path) for path in ancestors))


def _ledger_file(path: str, before: bytes | None, after: bytes | None, base: bytes | None) -> dict[str, Any]:
    """Return one changed-file item of the ledger entry: whole and owned digests before, after and of the base."""
    owned = lambda data: owned_or_none(TargetClass.MISSION, path, data)  # noqa: E731
    return {"path": path, "before_sha256": sha(before), "after_sha256": sha(after), "baseline_sha256": sha(base),
            "owned_before_sha256": owned(before), "owned_after_sha256": owned(after),
            "owned_baseline_sha256": owned(base)}
