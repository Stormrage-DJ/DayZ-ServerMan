"""Stage and publish manager records at the mission map commit point."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.windows.shared_files import replace_file
from ..domain.mission_map_applied import content_items, record_version
from ..domain.mission_map_ledger import parse_entry
from ..domain.mission_map_records import (
    PROVENANCE_FIELD, RecordShapeError, parse_applied, parse_baseline, parse_original, sealed,
)
from ..domain.models import RepositoryError, RevisionConflict
from .backup_verification import path_has_reparse
from .json_store import StagingPolicy
from .mission_map_commit_kinds import (
    MOVE_KINDS, VALUE_KINDS, check_original_kinds, check_value_identity, content_kind, discard_sibling, located,
    move_entries, move_record, parse_value, sibling_folder, stored_fields,
)
from .mission_map_contents import ContentStore
from .mission_map_journal import MapJournal, MapRecord, MissionMapJournalError, record_path_kind, record_staging
from .mission_map_journal_paths import adoption_kind, record_targets, sibling_kind
from .mission_map_journal_store import MissionMapJournalRepository
from .mission_map_layout import FILE_SETS, TargetClass
from .mission_map_records import (
    CREATE_ONCE_KINDS, ORIGINAL_KINDS, RecordConflict, RecordFile, record_files, restored_copy_file,
)
from .paths import PortablePaths


@dataclass(frozen=True)
class RecordUpdate:
    """Validated candidate record and content bytes, with the revision read by its preview."""

    kind: str
    target_path: str
    record: dict[str, Any]
    expected_revision: int | None = None
    contents: Mapping[str, bytes] = field(default_factory=dict)
    # Only a plan names the revision that it gets; None means the revision after the one found (10.34)
    revision: int | None = None


def parse_record(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Check one sealed manager record against its kind and managed file set."""
    if kind in VALUE_KINDS:
        return parse_value(kind, raw)
    if kind in ORIGINAL_KINDS or kind == "ledger":
        file_set = FILE_SETS[TargetClass(raw.get("target_class"))]
        if kind == "ledger":
            return parse_entry(raw, file_set)
        record = parse_original(raw, file_set)
        # Only the original of a new key names a source, and it always does (10.8)
        if kind == "copied_original" and PROVENANCE_FIELD not in record:
            raise RecordShapeError("a copied original names its source original")
        return record
    # An invalidated (10.10) or closed (10.35) last-applied record is read only by the sweeps, with the applied shape
    if kind not in ("baseline", "applied", "invalidated", "closed"):
        raise ValueError("commit record kind is invalid")
    return (parse_baseline if kind == "baseline" else parse_applied)(raw)


class CommitRecords:
    """Durable manager record staging, idempotent roll-forward and rollback cleanup."""

    def __init__(self, paths: PortablePaths, repository: MissionMapJournalRepository, fault_hook: Callable[[str, int], None]) -> None:
        """Bind the manager root, journal store and phase fault hook."""
        self.paths, self.repository, self.fault = paths, repository, fault_hook

    def stage(self, journal: MapJournal, updates: list[RecordUpdate]) -> None:
        """Stage checked records and contents without replacing any published record."""
        allowed = record_targets(journal, self.paths)
        if journal.records:
            raise ValueError("commit accepts no pre-staged record entries")
        seen: set[str] = set()
        fresh = self._fresh(updates)
        # Validate all candidates before the first staging write
        checked = []
        for update in updates:
            kind = allowed.get(update.target_path) or adoption_kind(journal, self.paths, update.target_path)
            if kind != record_path_kind(update.kind) or update.target_path in seen:
                raise ValueError("commit record path is invalid or duplicated")
            seen.add(update.target_path)
            record = parse_record(update.kind, sealed(update.record))
            self._check_identity(journal, update, record)
            target = self.paths.root / update.target_path
            if path_has_reparse(target.parent / "files"):
                raise OSError("editor content folder crosses a reparse point")
            # A record in the folder that this commit moves aside starts fresh; a rename has no record file
            fresh_target = fresh is not None and update.target_path.startswith(fresh)
            fresh_name = update.target_path.removeprefix(fresh or "")
            if update.target_path.startswith(sibling_folder(self.paths, journal) + "/") or (
                    fresh_target and sibling_kind(fresh_name, journal.operation_id) is None):
                raise ValueError("a fresh active folder gets only its three records and a restored copy")
            found = None if update.kind in MOVE_KINDS or fresh_target else self._file(target, kind).read()
            checked.append((update, record, found))
        replaced = check_original_kinds(self.paths.mission_map, journal, [
            (update.kind, update.target_path, record, found) for update, record, found in checked])
        prepared = []
        for update, record, found in checked:
            # A create-once record is kept; only a D5 replacement stages over a different original
            once = update.kind in CREATE_ONCE_KINDS | {"retired"}
            if once and found is not None and update.target_path not in replaced:
                if found[0]["manifest_sha256"] != record["manifest_sha256"]:
                    raise RecordConflict("a different create-once editor record already exists")
                continue
            if (None if found is None else found[1]) != update.expected_revision:
                raise RevisionConflict("Mission map record revision changed.")
            # Reapplying a plan never changes an unchanged working baseline
            if update.kind == "baseline" and found is not None and found[0]["manifest_sha256"] == record["manifest_sha256"]:
                continue
            # The fresh records stage in the sibling folder, which becomes the active folder at step 7
            place = update.target_path
            if fresh is not None and place.startswith(fresh):
                place = f"{sibling_folder(self.paths, journal)}/{place.removeprefix(fresh)}"
            entry = MapRecord(update.kind, place, record_staging(place, journal.operation_id), found is not None,
                              None if found is None else found[0]["manifest_sha256"], record["manifest_sha256"])
            revision = 0 if found is None else found[1] + 1
            # A plan that adoption takes over gets the revision after the one that load showed (10.2 D-1)
            if update.revision is not None:
                if update.kind != "plan" or update.revision < revision:
                    raise ValueError("only a plan names its revision, and never a lower one")
                revision = update.revision
            prepared.append((update, record, entry, revision))
        renames = [entry for _, _, entry, _ in prepared if entry.kind == "rename"]
        if renames:
            # The durable journal names the sibling folder before it exists, so recovery can always remove it
            journal.records.extend(move_entries(self.paths, journal, [entry for _, _, entry, _ in prepared]))
            self.repository.save(journal)
        # Track each candidate before its write so rollback can remove partial staging
        for index, (update, record, entry, revision) in enumerate(prepared):
            if not renames:
                journal.records.append(entry)
            if update.kind in MOVE_KINDS:
                continue
            store = ContentStore((self.paths.root / entry.target_path).parent)
            # The content of every stored item, also of Replace backups, is written before the journal (D4 step 2)
            if update.kind not in VALUE_KINDS | {"ledger"}:
                for item in content_items(content_kind(update.kind), record):
                    if item["existed"]:
                        data = update.contents.get(item["sha256"])
                        if path_has_reparse(store.folder / item["sha256"]):
                            raise OSError("editor content file crosses a reparse point")
                        if data is None or store.write(data) != item["sha256"]:
                            raise ValueError("commit record content is missing or has a different digest")
            stage = self.paths.root / entry.staging_path
            stage.parent.mkdir(parents=True, exist_ok=True)
            # The envelope version is the version of the record fields (8.7: version 2 of the last-applied record)
            payload = {"schema_version": record_version(record), "revision": revision,
                       **stored_fields(update.kind, record)}
            with stage.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            if self._manifest(stage, entry.kind) != entry.new_manifest_sha256:
                raise OSError("staged editor record failed verification")
            self.fault("AFTER_STAGE_RECORD", index)

    def roll_forward(self, journal: MapJournal) -> bool:
        """Publish each record after COMMITTING; equal published manifests count as complete."""
        try:
            return self._publish_records(journal)
        except (OSError, ValueError, RepositoryError, MissionMapJournalError):
            return False

    def _publish_records(self, journal: MapJournal) -> bool:
        """Replace checked staged records in order and durably mark each published manifest."""
        for index, entry in enumerate(journal.records):
            target, stage = located(self.paths, journal, entry), self.paths.root / entry.staging_path
            # A replace or a folder move may have completed before its journal state was saved
            if entry.kind in MOVE_KINDS:
                if entry.state != "PUBLISHED":
                    if not move_record(self.paths, journal, entry):
                        return False
                    self.fault("AFTER_RENAME_FOLDER", index)
            elif (current := self._manifest(target, entry.kind)) != entry.new_manifest_sha256:
                if entry.state == "PUBLISHED" or current != entry.old_manifest_sha256:
                    return False
                if self._manifest(stage, entry.kind) != entry.new_manifest_sha256:
                    return False
                replace_file(stage, target)
                self.fault("AFTER_PUBLISH_RECORD", index)
                if self._manifest(target, entry.kind) != entry.new_manifest_sha256:
                    return False
            entry.state = "PUBLISHED"
            self.repository.save(journal)
        return True

    def rollback(self, journal: MapJournal) -> bool:
        """Discard staged records after rollback, preserving every published record."""
        try:
            return self._discard(journal)
        except (OSError, ValueError, RepositoryError):
            return False

    def _discard(self, journal: MapJournal) -> bool:
        """Remove commit staging and unused contents while preserving published record content."""
        for entry in journal.records:
            stage = self.paths.root / entry.staging_path
            # The staging path of the second folder move is the sibling folder; the reverse step removes it
            if entry.kind not in MOVE_KINDS and stage.exists():
                stage.unlink()
        self.sweep(journal)
        # The reverse step runs only for a journal that never reached its commit point
        if journal.phase in ("COMMITTING", "COMMITTED") or not any(entry.kind == "rename" for entry in journal.records):
            return True
        return discard_sibling(self.paths.root / sibling_folder(self.paths, journal), journal.operation_id)

    def sweep(self, journal: MapJournal) -> None:
        """Remove unused contents only after the commit or rollback reaches a terminal state."""
        for folder in {(self.paths.root / entry.target_path).parent for entry in journal.records}:
            if path_has_reparse(folder / "files"):
                raise OSError("editor content folder crosses a reparse point")
            keep: set[str] = set()
            # Folder contents belong jointly to its records, also to the superseded and restored copies (10.8)
            for path, kind in record_files(folder):
                found = self._file(path, kind).read()
                if found is not None:
                    items = content_items(content_kind(kind), found[0])
                    keep.update(item["sha256"] for item in items if item["existed"])
            ContentStore(folder).sweep(keep)

    def _manifest(self, path: Path, kind: str) -> str | None:
        """Read a verified record manifest, or None when no published record exists."""
        found = self._file(path, kind).read()
        return None if found is None else found[0]["manifest_sha256"]

    def _file(self, path: Path, kind: str) -> RecordFile:
        """Read a record without interpreting commit staging as an interrupted plan save."""
        return RecordFile(path, lambda raw: parse_record(kind, raw), StagingPolicy.OBSERVER)

    def _check_identity(self, journal: MapJournal, update: RecordUpdate, record: dict[str, Any]) -> None:
        """Require record content identity to agree with its journal and derived destination."""
        if update.kind in VALUE_KINDS:
            check_value_identity(self.paths, journal, update.kind, update.target_path, record)
        if update.kind in ("baseline", "applied"):
            if (record["profile_id"], record["mission_key"], record["mission_root"]) != (
                    journal.profile_id, journal.mission_key, journal.mission_root):
                raise ValueError("commit record association differs from its journal")
        if update.kind in CREATE_ONCE_KINDS:
            target_class = TargetClass(record["target_class"])
            key = journal.mission_key if target_class is TargetClass.MISSION else journal.runtime_profile_key
            place = update.target_path if f"/{key}/" in update.target_path else None
            # A restored copy lives in the association folder of the restoring profile, named by its class
            if update.kind == "restored_copy":
                place = self.paths.relative(restored_copy_file(self.paths.mission_map, journal.profile_id,
                                                               journal.mission_key, target_class, journal.operation_id))
            if record["target_key"] != key or update.target_path != place:
                raise ValueError("commit record target differs from its journal")
        # An adoption carries the last-applied record of its source with the operation of that record (10.35)
        if update.kind == "ledger" or (update.kind == "applied" and journal.operation_kind != "adopt"):
            if (record["operation_id"], record["operation_kind"], record["profile_id"]) != (
                    journal.operation_id, journal.operation_kind, journal.profile_id):
                raise ValueError("commit record operation differs from its journal")

    def _fresh(self, updates: list[RecordUpdate]) -> str | None:
        """Return the active folder, with a final slash, when the commit renames it; one rename at most."""
        renames = [update for update in updates if update.kind == "rename"]
        if len(renames) > 1:
            raise ValueError("a commit renames the active association folder at most once")
        return f"{renames[0].record.get('source')}/" if renames else None
