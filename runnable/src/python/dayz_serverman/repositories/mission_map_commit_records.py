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
from ..domain.mission_map_records import parse_applied, parse_baseline, parse_original, sealed
from ..domain.models import RepositoryError, RevisionConflict
from .backup_verification import path_has_reparse
from .json_store import StagingPolicy
from .mission_map_contents import ContentStore
from .mission_map_journal import MapJournal, MapRecord, MissionMapJournalError, record_staging
from .mission_map_journal_paths import record_targets
from .mission_map_journal_store import MissionMapJournalRepository
from .mission_map_layout import FILE_SETS, TargetClass
from .mission_map_records import RecordConflict, RecordFile
from .paths import PortablePaths


@dataclass(frozen=True)
class RecordUpdate:
    """Validated candidate record and content bytes, with the revision read by its preview."""

    kind: str
    target_path: str
    record: dict[str, Any]
    expected_revision: int | None = None
    contents: Mapping[str, bytes] = field(default_factory=dict)


def parse_record(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Check one sealed manager record against its kind and managed file set."""
    if kind in ("original", "ledger"):
        file_set = FILE_SETS[TargetClass(raw.get("target_class"))]
        return (parse_original if kind == "original" else parse_entry)(raw, file_set)
    if kind not in ("baseline", "applied"):
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
        # Validate all candidates before the first staging write
        prepared = []
        for update in updates:
            if allowed.get(update.target_path) != update.kind or update.target_path in seen:
                raise ValueError("commit record path is invalid or duplicated")
            seen.add(update.target_path)
            record = parse_record(update.kind, sealed(update.record))
            self._check_identity(journal, update, record)
            target = self.paths.root / update.target_path
            if path_has_reparse(target.parent / "files"):
                raise OSError("editor content folder crosses a reparse point")
            found = self._file(target, update.kind).read()
            if update.kind in ("original", "ledger") and found is not None:
                if found[0]["manifest_sha256"] != record["manifest_sha256"]:
                    raise RecordConflict("a different create-once editor record already exists")
                continue
            if (None if found is None else found[1]) != update.expected_revision:
                raise RevisionConflict("Mission map record revision changed.")
            # Reapplying a plan never changes an unchanged working baseline
            if update.kind == "baseline" and found is not None and found[0]["manifest_sha256"] == record["manifest_sha256"]:
                continue
            entry = MapRecord(update.kind, update.target_path, record_staging(update.target_path, journal.operation_id),
                              found is not None, None if found is None else found[0]["manifest_sha256"],
                              record["manifest_sha256"])
            prepared.append((update, record, entry, 0 if found is None else found[1] + 1))
        # Track each candidate before its write so rollback can remove partial staging
        for index, (update, record, entry, revision) in enumerate(prepared):
            journal.records.append(entry)
            store = ContentStore((self.paths.root / entry.target_path).parent)
            # The content of every stored item, also of Replace backups, is written before the journal (D4 step 2)
            if update.kind != "ledger":
                for item in content_items(update.kind, record):
                    if item["existed"]:
                        data = update.contents.get(item["sha256"])
                        if path_has_reparse(store.folder / item["sha256"]):
                            raise OSError("editor content file crosses a reparse point")
                        if data is None or store.write(data) != item["sha256"]:
                            raise ValueError("commit record content is missing or has a different digest")
            stage = self.paths.root / entry.staging_path
            stage.parent.mkdir(parents=True, exist_ok=True)
            # The envelope version is the version of the record fields (8.7: version 2 of the last-applied record)
            payload = {"schema_version": record_version(record), "revision": revision, **record}
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
            target, stage = self.paths.root / entry.target_path, self.paths.root / entry.staging_path
            # A replace may have completed before its journal state was saved
            current = self._manifest(target, entry.kind)
            if current != entry.new_manifest_sha256:
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
            if stage.exists():
                stage.unlink()
        self.sweep(journal)
        return True

    def sweep(self, journal: MapJournal) -> None:
        """Remove unused contents only after the commit or rollback reaches a terminal state."""
        for folder in {(self.paths.root / entry.target_path).parent for entry in journal.records}:
            if path_has_reparse(folder / "files"):
                raise OSError("editor content folder crosses a reparse point")
            keep: set[str] = set()
            # Association contents belong jointly to the baseline and last-applied records
            for name, kind in (("original.json", "original"), ("baseline.json", "baseline"), ("applied.json", "applied")):
                found = self._file(folder / name, kind).read()
                if found is not None:
                    keep.update(item["sha256"] for item in content_items(kind, found[0]) if item["existed"])
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
        if update.kind in ("baseline", "applied"):
            if (record["profile_id"], record["mission_key"], record["mission_root"]) != (
                    journal.profile_id, journal.mission_key, journal.mission_root):
                raise ValueError("commit record association differs from its journal")
        if update.kind in ("original", "ledger"):
            key = journal.mission_key if record["target_class"] == "mission" else journal.runtime_profile_key
            if record["target_key"] != key or f"/{key}/" not in update.target_path:
                raise ValueError("commit record target differs from its journal")
        if update.kind in ("applied", "ledger"):
            if (record["operation_id"], record["operation_kind"], record["profile_id"]) != (
                    journal.operation_id, journal.operation_kind, journal.profile_id):
                raise ValueError("commit record operation differs from its journal")
