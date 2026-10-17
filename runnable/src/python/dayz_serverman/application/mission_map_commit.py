"""Commit validated mission map candidates and their records in the accepted A3 lock order."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path

from ..domain.models import RepositoryError
from ..repositories.mission_map_commit_capture import check_capture
from ..domain.mission_map_records import sealed
from ..repositories.backup_verification import path_has_reparse
from ..repositories.mission_map_commit_records import CommitRecords, RecordUpdate, parse_record
from ..repositories.mission_map_commit_staging import stage_groups
from ..repositories.mission_map_commit_validation import validate_record_links
from ..repositories.mission_map_journal import MapJournal, MapRecord, MissionMapJournalError, record_staging
from ..repositories.mission_map_journal_paths import journal_paths_safe, record_targets
from ..repositories.mission_map_journal_store import MissionMapJournalRepository, parse_journal
from ..repositories.mission_map_layout import TargetClass, association_folder, original_folder, RETIRED_FILE
from ..repositories.mission_map_owned import writable
from ..repositories.mission_map_publication import MissionMapPublication, MissionMapPublicationError
from ..repositories.paths import PortablePaths
from ..repositories.publication_contracts import RecoveryHooks
from ..repositories.restore_paths import RestorePathError, group_old_matches
from .folder_writer_scope import WriterScope
from .installation_guard import InstallationGuard
from .lifecycle_ports import ServerFolderWriterPort
from .mission_map_locks import AssociationLocks

# Journal phases at or after the commit point (D4): only a roll-forward is allowed there
COMMIT_POINT_PHASES = frozenset(("COMMITTING", "COMMITTED"))


class MissionMapCommit:
    """The commit step called from exclusive operation-lane work; candidate generation stays with the caller."""

    def __init__(
        self, paths: PortablePaths, journals: MissionMapJournalRepository, guard: InstallationGuard,
        folder_writer: ServerFolderWriterPort | None, locks: AssociationLocks, *,
        owned_digest: Callable[[TargetClass, str, bytes], str | None],
        check_reserved: Callable[[TargetClass, str, bytes], None],
        fault_hook: Callable[[str, int], None] | None = None,
    ) -> None:
        """Require the process's shared association locks and the authoritative adapter checks."""
        self.paths, self.journals, self.guard = paths, journals, guard
        self.folder_writer, self.locks = folder_writer, locks
        self.owned_digest, self.check_reserved = owned_digest, check_reserved
        self.fault = fault_hook or (lambda _phase, _index: None)

    def commit_point(self, operation_id: str) -> str | None:
        """Return where the journal of a failed Apply or restore stands (AR-4).

        The value is "active" at or after COMMITTING, and "retired" when it was retired as committed. It is None
        when no journal exists, or when the journal was rolled back before COMMITTING.
        """
        try:
            retired = self.journals.retired_path_for(operation_id)
            if retired.exists():
                return "retired" if self.journals.load(retired).result == "COMMITTED" else None
            active = self.journals.path_for(operation_id)
            if active.exists() and self.journals.load(active).phase in COMMIT_POINT_PHASES:
                return "active"
        except (OSError, ValueError, RuntimeError):
            # A journal that cannot be read is a case of the 8.5 recovery, so the lane needs recovery
            return "active"
        return None

    def commit(
        self, journal: MapJournal, updates: list[RecordUpdate], candidates: Mapping[tuple[str, str], bytes], *,
        revalidate: Callable[[], None], existing_profiles: set[str],
    ) -> dict[str, object]:
        """Commit D4 steps 1 to 8 under mutex, STOPPED proof, writer and innermost association lock."""
        # The operation-lane caller holds the lane before the installation guard
        with self.guard.stopped(Path(journal.dayz_root)):
            revalidate()
            self.fault("AFTER_REVALIDATE", -1)
            writer = WriterScope(self.folder_writer)
            with writer.held_for(), self.locks.hold(journal.profile_id, journal.mission_key):
                # A save may finish while the writer is acquired; prove the preview again under its lock
                revalidate()
                self._preflight(journal, updates, candidates)
                check_capture(self.paths, journal, updates, existing_profiles, self.owned_digest, self.check_reserved)
                return self._commit_locked(journal, updates, candidates, writer)

    def commit_records(
        self, journal: MapJournal, updates: list[RecordUpdate], *, revalidate: Callable[[], None],
        preflight: Callable[[MapJournal, list[RecordUpdate]], None],
    ) -> dict[str, object]:
        """Commit a journal of manager-root records only, as the adoption does (AR-3, leaf 10.35).

        The installation mutex comes first, without a STOPPED proof, because no DayZ-side file changes. Then the
        review again, the folder writer side, the association lock, the caller's preflight and the same D4 body.
        """
        with self.guard.mutex(Path(journal.dayz_root)):
            revalidate()
            self.fault("AFTER_REVALIDATE", -1)
            writer = WriterScope(self.folder_writer)
            with writer.held_for(), self.locks.hold(journal.profile_id, journal.mission_key):
                # A plan save may finish while the writer side is taken; review again under the association lock
                revalidate()
                if journal.groups:
                    raise MissionMapPublicationError("PATH_INVALID", "A records-only commit has no mission file.")
                preflight(journal, updates)
                return self._commit_locked(journal, updates, {}, writer)

    def _preflight(self, journal: MapJournal, updates: list[RecordUpdate], candidates: Mapping[tuple[str, str], bytes]) -> None:
        """Refuse unsafe paths, reused operations, retired associations and missing record candidates."""
        # Refuse a malformed journal before any write, also one without a DayZ-side group (Architect 01:18:47)
        parse_journal(_probe(journal, updates), journal.operation_id)
        if journal.phase != "PREPARED" or not journal_paths_safe(journal, Path(journal.dayz_root), self.paths):
            raise MissionMapPublicationError("PATH_INVALID", "Mission map commit paths are unsafe.")
        if self.journals.path_for(journal.operation_id).exists() or self.journals.retired_path_for(journal.operation_id).exists():
            raise MissionMapPublicationError("REVISION_CONFLICT", "Mission map operation already has a journal.")
        folder = association_folder(self.paths.mission_map, journal.profile_id, journal.mission_key)
        if (folder / RETIRED_FILE).exists():
            raise MissionMapPublicationError("REVISION_CONFLICT", "Mission map association requires adoption.")
        # A ledger entry lists changed files only, so a commit without a DayZ-side group has none (8.7)
        required = {"baseline", "applied"} | ({"ledger"} if journal.groups else set())
        if not required <= {update.kind for update in updates}:
            raise ValueError("commit requires baseline, applied and ledger record candidates")
        # Candidate records must have safe derived paths before capture reads or staging writes
        allowed = record_targets(journal, self.paths)
        for update in updates:
            if allowed.get(update.target_path) != update.kind or path_has_reparse(self.paths.root / update.target_path):
                raise MissionMapPublicationError("PATH_INVALID", "Mission map record path is unsafe.")
            parse_record(update.kind, sealed(update.record))
        keys = [journal.mission_key] + ([journal.runtime_profile_key] if journal.runtime_profile_key else [])
        for key in keys:
            original = original_folder(self.paths.mission_map, key) / "original.json"
            if not original.exists() and not any(update.kind == "original" and update.record["target_key"] == key for update in updates):
                raise ValueError("commit requires the uncaptured target's protected original")
        required = {(group.target_class, group.relative_path) for group in journal.groups if group.new_exists}
        if set(candidates) != required:
            raise ValueError("commit candidate file set differs from its journal")
        for group in journal.groups:
            if group.new_exists and hashlib.sha256(candidates[(group.target_class, group.relative_path)]).hexdigest() != group.new_digest:
                raise ValueError("commit candidate digest differs from its preview")
            # The rule applies to the file and to the written state, never to the bytes that an absence removes.
            # A writable file may lose a damaged owned block. A captured-only file is refused (Architect 07:07:28, 8.8).
            target_class = TargetClass(group.target_class)
            if group.new_exists:
                written = candidates[(group.target_class, group.relative_path)]
                projected = self.owned_digest(target_class, group.relative_path, written) is not None
            else:
                projected = writable(target_class, group.relative_path)
            if not projected:
                raise ValueError("commit refuses a write without an owned-value projection")
        validate_record_links(self.paths, journal, updates, candidates, self.owned_digest)

    def _commit_locked(
        self, journal: MapJournal, updates: list[RecordUpdate], candidates: Mapping[tuple[str, str], bytes], writer: WriterScope,
    ) -> dict[str, object]:
        """Keep the journal durable before DayZ staging and every record unpublished until COMMITTING."""
        publication = MissionMapPublication(self.paths, self.journals, writer, fault_hook=self.fault)
        records = CommitRecords(self.paths, self.journals, self.fault)
        try:
            # Stage manager records first, then persist the journal before any DayZ-side byte
            records.stage(journal, updates)
            if not journal_paths_safe(journal, Path(journal.dayz_root), self.paths):
                raise MissionMapPublicationError("PATH_INVALID", "Mission map record paths are unsafe.")
            # The journal round-trips through the 8.5 load parser before its durable save
            parse_journal(journal.to_dict(), journal.operation_id)
            self.fault("AFTER_STAGE_RECORDS", -1)
            self.journals.save(journal)
            self.fault("AFTER_PREPARED", -1)
            stage_groups(journal, candidates, self.fault)
            publication.publish_groups(journal)
            publication.advance(journal)
            self.fault("AFTER_PUBLISHED", -1)
            publication.advance(journal)
            self.fault("AFTER_COMMITTING", -1)
            if not records.roll_forward(journal):
                raise MissionMapPublicationError("RECOVERY_REQUIRED", "Mission map records require recovery.", recovery_required=True)
            publication.advance(journal)
            self.fault("AFTER_COMMITTED", -1)
            publication.cleanup(journal)
            if not records.rollback(journal):
                raise MissionMapPublicationError("RECOVERY_REQUIRED", "Mission map content cleanup requires recovery.", recovery_required=True)
            publication.retire(journal)
            return {"operation_id": journal.operation_id, "result": "COMMITTED"}
        except (OSError, ValueError, RepositoryError, MissionMapJournalError, MissionMapPublicationError, RestorePathError) as error:
            # Re-read the durable phase; an interrupted save may already have replaced its journal
            active = self.journals.path_for(journal.operation_id)
            if active.exists():
                durable = self.journals.load(active)
                report = publication.inspect(Path(journal.dayz_root), RecoveryHooks(records.roll_forward, records.rollback))
                if durable.phase in ("COMMITTING", "COMMITTED"):
                    if not report["blocked"]:
                        return {"operation_id": journal.operation_id, "result": "COMMITTED"}
                    raise MissionMapPublicationError("RECOVERY_REQUIRED", "Apply reached its commit point; review recovery status.",
                                                     recovery_required=bool(report["blocked"])) from error
                if report["blocked"]:
                    raise MissionMapPublicationError("RECOVERY_REQUIRED", "Mission map rollback requires recovery.", recovery_required=True) from error
            else:
                records.rollback(journal)
            if isinstance(error, MissionMapPublicationError) and error.code == "TARGET_CHANGED":
                changed = next((group.relative_path for group in journal.groups if group.state == "READY"
                                and not group_old_matches(group)), "a mission file")
                conflict = MissionMapPublicationError("REVISION_CONFLICT", f"Apply stopped because {changed} changed after the preview. "
                                                     "No change was kept, and that file keeps its current content. Review the preview again.")
                conflict.details, conflict.retryable = {"reason": "target_changed"}, True
                raise conflict from error
            raise


def _probe(journal: MapJournal, updates: list[RecordUpdate]) -> dict[str, object]:
    """Return the journal as the load parser sees it; without a group, with one entry of its first record.

    A journal without a DayZ-side group names only records, and staging adds them. The probe entry lets the
    parser check every other field before the first write. The real journal is parsed again before its save.
    """
    value = journal.to_dict()
    if not journal.groups and not journal.records and updates:
        target = updates[0].target_path
        value["records"] = [MapRecord(updates[0].kind, target, record_staging(target, journal.operation_id),
                                      False, None, "0" * 64).to_dict()]
    return value
