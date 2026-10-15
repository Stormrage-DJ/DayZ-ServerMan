"""The journal, the records and the candidate of one medical toggle on an editor-managed mission (8.13, D3, D4).

D4 "Medical Operation Kind": one schema-1 journal of kind `medical`. Its plan
fields are null and it binds the reviewed zone fingerprint. Its only DayZ-side
group is `mapgroupproto.xml`. It stages a new last-applied record and the
ledger entry of the changed file, and no original and no working baseline.

Values of the kind:

- Journal: `operation_kind` `medical`; `plan_id`, `plan_revision` and
  `plan_fingerprint` null; `preview_fingerprint` the zone fingerprint.
- Last-applied record (version 2): `operation_kind` `medical`. The applied plan
  does not change. So the plan fields, the global value, the plan objects and
  the excluded encounters stay those of the current record. The baseline
  revision and manifest are those of the unchanged working baseline.
- "Files after" are those of the current record, with the new digests of
  `mapgroupproto.xml`. "Files before" hold `mapgroupproto.xml` before the toggle
  with its content, so Restore previous state returns that state. The names
  and keys do not change, so `generated_groups` and `generated_groups_before`
  are both the realization of the current record.
- Ledger entry: kind `medical`, `output` true (the generated groups are editor
  output), one file item for `mapgroupproto.xml` with the baseline digests of
  the working baseline.

The working baseline passes to the commit step unchanged, with its contents.
The commit checks its links and its reserved names. Staging skips an equal baseline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..domain.medical_editor_texts import MEDICAL_KIND, MISMATCH_TEXT, ZoneToggleRefused
from ..domain.mission_map_applied import GROUPS_BEFORE_FIELD, GROUPS_FIELD
from ..domain.mission_map_records import sealed
from ..repositories.json_store import StagingPolicy
from ..repositories.mission_map_commit_records import RecordUpdate
from ..repositories.mission_map_journal import MapGroup, MapJournal
from ..repositories.mission_map_journal_paths import group_staging_path, recovery_copy_path, target_folder
from ..repositories.mission_map_journal_store import recovery_folder
from ..repositories.mission_map_layout import APPLIED_FILE, BASELINE_FILE, TargetClass, association_folder, ledger_folder
from ..repositories.mission_map_mapgroup_read import PROTO
from ..repositories.mission_map_records import AssociationRecordStore
from ..repositories.paths import PortablePaths
from .medical_editor_review import ZoneReview, ZoneTarget
from .mission_map_apply_records import owned_or_none


@dataclass(frozen=True)
class TogglePublication:
    """One toggle commit: the PREPARED journal, the record candidates and the new `mapgroupproto.xml`."""

    journal: MapJournal
    updates: list[RecordUpdate]
    candidates: dict[tuple[str, str], bytes]


def build_toggle(review: ZoneReview, target: ZoneTarget, *, operation_id: str, now: str,
                 paths: PortablePaths) -> TogglePublication:
    """Return the commit of one toggle that changes `mapgroupproto.xml`."""
    applied_found, baseline_found = review.records.applied, review.records.baseline
    if applied_found is None or baseline_found is None:
        raise ZoneToggleRefused("records_missing", MISMATCH_TEXT)
    (applied, applied_revision), (baseline, baseline_revision) = applied_found, baseline_found
    key, pid, before, after = review.key, target.profile_id, review.current, review.candidate
    folder = target_folder(target.root, target.mission_root)
    journal = MapJournal(operation_id, MEDICAL_KIND, pid, target.mission_root, key, None, None, str(target.root),
                         None, None, None, review.fingerprint, target.profile_revision, target.settings_revision,
                         [_group(paths, folder, operation_id, before, after)], [])
    association = association_folder(paths.mission_map, pid, key)
    store = AssociationRecordStore(paths.mission_map, pid, key, "baseline", staging=StagingPolicy.OBSERVER)
    base_contents = {item["sha256"]: store.contents.read(item["sha256"]) for item in baseline["files"] if item["existed"]}
    base_proto = next((item for item in baseline["files"] if item["target_key"] == key and item["path"] == PROTO), None)
    if base_proto is None:
        raise ZoneToggleRefused("records_missing", MISMATCH_TEXT)
    base_data = base_contents.get(base_proto["sha256"]) if base_proto["existed"] else None
    files_after = [dict(item, sha256=_sha(after), owned_sha256=_owned(after))
                   if (item["target_key"], item["path"]) == (key, PROTO) else item for item in applied["files_after"]]
    record = {name: value for name, value in applied.items() if name != "manifest_sha256"}
    record.update(operation_id=operation_id, operation_kind=MEDICAL_KIND, applied_at=now,
                  baseline_revision=baseline_revision, baseline_manifest_sha256=baseline["manifest_sha256"],
                  files_after=files_after,
                  files_before=[{"target_key": key, "path": PROTO, "existed": True, "sha256": _sha(before),
                                 "size": len(before)}],
                  **{GROUPS_FIELD: applied[GROUPS_FIELD], GROUPS_BEFORE_FIELD: applied[GROUPS_FIELD]})
    entry = {"operation_id": operation_id, "operation_kind": MEDICAL_KIND, "recorded_at": now, "profile_id": pid,
             "target_class": "mission", "target_key": key, "display_path": str(target.mission),
             "mission_root": target.mission_root, "output": True, "carried_operation_id": None,
             "files": [{"path": PROTO, "before_sha256": _sha(before), "after_sha256": _sha(after),
                        "baseline_sha256": base_proto["sha256"], "owned_before_sha256": _owned(before),
                        "owned_after_sha256": _owned(after), "owned_baseline_sha256": _owned(base_data)}]}
    updates = [
        RecordUpdate("baseline", paths.relative(association / BASELINE_FILE), baseline, baseline_revision,
                     base_contents),
        RecordUpdate("applied", paths.relative(association / APPLIED_FILE), sealed(record), applied_revision,
                     {_sha(before): before}),
        RecordUpdate("ledger", paths.relative(ledger_folder(paths.mission_map, key) / f"{operation_id}.json"),
                     sealed(entry)),
    ]
    return TogglePublication(journal, updates, {("mission", PROTO): after})


def _group(paths: PortablePaths, folder: Path, operation_id: str, old: bytes, new: bytes) -> MapGroup:
    """Return the journal group of `mapgroupproto.xml`, with the derived staging and recovery paths (D4)."""
    target = folder / PROTO
    recovery = recovery_copy_path(recovery_folder(paths).resolve(), operation_id, "mission", PROTO)
    return MapGroup("mission", PROTO, str(target), str(group_staging_path(target, operation_id, ())), str(recovery),
                    True, _sha(old), True, _sha(new))


def _owned(data: bytes | None) -> str | None:
    """Return the owned-value digest of `mapgroupproto.xml`, or None for a damaged block."""
    return owned_or_none(TargetClass.MISSION, PROTO, data)


def _sha(data: bytes | None) -> str | None:
    """Return SHA-256 of the bytes, or None for an absent file."""
    return None if data is None else hashlib.sha256(data).hexdigest()

