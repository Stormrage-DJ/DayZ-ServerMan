"""Verified staging, journaled publication, and restore compensation."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain.backups import BackupManifest
from ..domain.restores import RestoreGroup, RestoreJournal, RestoreTarget
from .backup_verification import sha256_file
from .restore_journal import RestoreJournalError, RestoreJournalRepository
from .restore_preparation import prepare_groups
from .restore_paths import (
    RestorePathError,
    copy_verified,
    group_old_matches,
    journal_paths_safe,
    matches,
    old_state_matches,
    remove_staging,
    remove_stage_tree,
    safe_root,
    safe_target,
    target_relative,
)


class RestoreStorageError(RuntimeError):
    """Raised when restore staging, publication, or compensation fails."""
    def __init__(self, code: str, message: str, *, recovery_required: bool = False) -> None:
        """Store the error code and whether recovery is required."""
        self.code = code
        self.recovery_required = recovery_required
        super().__init__(message)


class RestoreStorage:
    """Stage, publish, and compensate restore of snapshot payloads."""
    def __init__(
        self,
        *,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
        fault_hook: Callable[[str, int], None] | None = None,
    ) -> None:
        """Store the disk-usage probe and the optional fault-injection hook."""
        self._disk_usage = disk_usage
        # Default the fault hook to a no-op for callers without injection
        self._fault_hook = fault_hook or (lambda _phase, _index: None)

    def targets(
        self,
        snapshot: Path,
        manifest: BackupManifest,
        dayz_root: Path,
        runtime_profile: str | None = None,
    ) -> tuple[RestoreTarget, ...]:
        """Plan the restore targets for one manifest against the DayZ root."""
        root = safe_root(dayz_root)
        result: list[RestoreTarget] = []
        # Derive each target path and its pre-restore digest
        for entry in manifest.entries:
            relative = target_relative(entry.path, runtime_profile)
            target = safe_target(
                root, relative, allow_missing_parents=entry.path.startswith("runtime-profile/"),
            )
            current = sha256_file(target) if target.exists() else None
            result.append(RestoreTarget(
                entry.path,
                relative.as_posix(),
                "REPLACE" if current is not None else "CREATE",
                current,
                entry.sha256,
                entry.size,
            ))
            # Verify the snapshot payload still matches the manifest
            source = snapshot / entry.path
            if sha256_file(source) != entry.sha256:
                raise RestoreStorageError("BACKUP_INTEGRITY_FAILED", "Snapshot payload changed.")
        # Case-folded targets must not collide on Windows file systems
        folded = [item.target_relative.casefold() for item in result]
        if len(folded) != len(set(folded)):
            raise RestoreStorageError("PATH_INVALID", "Restore targets overlap on Windows.")
        return tuple(result)

    def restore(
        self,
        snapshot: Path,
        manifest: BackupManifest,
        dayz_root: Path,
        recovery_root: Path,
        journal_repository: RestoreJournalRepository,
        operation_id: str,
        checkpoint: Callable[[str, int], None],
        runtime_profile: str | None = None,
    ) -> dict[str, object]:
        """Restore a snapshot and return a summary of the committed operation."""
        # Plan and verify targets before any file is touched
        targets = self.targets(snapshot, manifest, dayz_root, runtime_profile)
        checkpoint("VERIFY_SOURCE", 10)
        # Stage payloads and recovery copies before publication starts
        groups = prepare_groups(
            snapshot, targets, dayz_root, recovery_root, operation_id, checkpoint,
            self._disk_usage, copy_verified,
        )
        # Journal the prepared operation so a crash can be recovered
        journal = RestoreJournal(
            operation_id, manifest.backup_id, manifest.profile_id, manifest.manifest_digest,
            "PREPARED", False, False, False, groups,
            runtime_profile=runtime_profile,
        )
        try:
            checkpoint("WRITE_JOURNAL", 60)
            journal_repository.save(journal)
            self._publish(journal, journal_repository)
        except Exception as error:
            # Nothing was published yet, so staging alone is enough to undo
            if not journal.publication_started:
                self._cleanup_artifacts(journal, recovery_root)
                raise
            if self._compensate(journal, journal_repository):
                self._cleanup_artifacts(journal, recovery_root)
                try:
                    journal_repository.retire(journal)
                except Exception as retirement_error:
                    raise RestoreStorageError(
                        "RECOVERY_REQUIRED",
                        "Restore rolled back but its journal could not be retired.",
                        recovery_required=True,
                    ) from retirement_error
                raise RestoreStorageError("RESTORE_FAILED", "Restore failed and prior files were restored.") from error
            raise RestoreStorageError(
                "RECOVERY_REQUIRED",
                "Restore publication failed and prior files could not be proven.",
                recovery_required=True,
            ) from error
        # Prove every committed target before the journal is retired
        if not all(matches(Path(group.target_path), group.new_digest) for group in groups):
            raise RestoreStorageError(
                "RECOVERY_REQUIRED", "Committed restore targets could not be proven.",
                recovery_required=True,
            )
        try:
            journal_repository.retire(journal)
        except Exception as error:
            raise RestoreStorageError(
                "RECOVERY_REQUIRED", "Committed restore journal could not be retired.",
                recovery_required=True,
            ) from error
        return {
            "backup_id": manifest.backup_id,
            "profile_id": manifest.profile_id,
            "manifest_digest": manifest.manifest_digest,
            "restored_count": len(groups),
            "journal_state": journal.phase,
        }

    def inspect(
        self,
        journal_repository: RestoreJournalRepository,
        dayz_root: Path,
        recovery_root: Path,
    ) -> dict[str, object]:
        """Resolve interrupted journals and report whether any still block."""
        diagnostics: list[dict[str, object]] = []
        blocked = False
        # Inspect unresolved journals before already-resolved ones
        records = sorted(
            journal_repository.records(),
            key=lambda item: (item[1] is None, item[1].resolved if item[1] is not None else False),
        )
        for path, journal in records:
            # A journal with unsafe paths can never be acted on automatically
            if journal is None or not journal_paths_safe(journal, dayz_root, recovery_root):
                blocked = True
                diagnostics.append(_recovery_diagnostic("A restore journal is invalid or unsafe."))
                continue
            # A committed journal only needs its targets proven and archived
            if journal.committed and journal.phase == "COMMITTED":
                safe = all(matches(Path(group.target_path), group.new_digest) for group in journal.groups)
                if safe:
                    safe = self._retire(journal_repository, journal)
            # A rolled-back journal only needs its prior state proven and archived
            elif journal.resolved and journal.phase == "ROLLED_BACK":
                safe = old_state_matches(journal.groups)
                if safe:
                    safe = self._retire(journal_repository, journal)
            # An unpublished journal that still holds the prior state is closed out
            elif not journal.publication_started and old_state_matches(journal.groups):
                journal.phase, journal.resolved, journal.result = "ROLLED_BACK", True, "ROLLED_BACK"
                journal_repository.save(journal)
                self._cleanup_artifacts(journal, recovery_root)
                safe = self._retire(journal_repository, journal)
            else:
                # Otherwise compensate the interrupted publication in place
                safe = self._compensate(journal, journal_repository)
                if safe:
                    self._cleanup_artifacts(journal, recovery_root)
                    safe = self._retire(journal_repository, journal)
            if not safe:
                blocked = True
                diagnostics.append(_recovery_diagnostic(
                    "An interrupted restore could not prove the prior or restored state.",
                ))
        return {"blocked": blocked, "diagnostics": diagnostics}

    def _publish(self, journal: RestoreJournal, repository: RestoreJournalRepository) -> None:
        """Publish every group in order, journaling each state change."""
        # Mark the journal as publishing before the first file moves
        journal.phase, journal.publication_started = "PUBLISHING", True
        repository.save(journal)
        for index, group in enumerate(journal.groups):
            group.state = "PUBLISHING"
            repository.save(journal)
            self._fault_hook("BEFORE_PUBLISH", index)
            # Recreate missing ancestors so the staged file can move in
            for ancestor in group.created_ancestors:
                path = Path(ancestor)
                if not path.exists():
                    self._fault_hook("BEFORE_CREATE_ANCESTOR", index)
                    path.mkdir()
                    self._fault_hook("AFTER_CREATE_ANCESTOR", index)
            Path(group.staging_path).replace(group.target_path)
            self._fault_hook("AFTER_PUBLISH", index)
            # Verify the published bytes before recording the group as published
            if not matches(Path(group.target_path), group.new_digest):
                raise OSError("published restore target failed verification")
            group.state = "PUBLISHED"
            repository.save(journal)
        # Record the terminal committed state
        journal.phase, journal.committed, journal.resolved, journal.result = (
            "COMMITTED", True, True, "COMMITTED",
        )
        repository.save(journal)

    def _compensate(
        self,
        journal: RestoreJournal,
        repository: RestoreJournalRepository,
    ) -> bool:
        """Return the prior file state after an interrupted publication."""
        journal.phase = "COMPENSATING"
        try:
            repository.save(journal)
            # Walk groups in reverse so later publications are undone first
            for reverse_index, group in enumerate(reversed(journal.groups)):
                target = Path(group.target_path)
                self._fault_hook("BEFORE_COMPENSATE", reverse_index)
                # An untouched target needs no compensation
                if group_old_matches(group):
                    group.state = "COMPENSATED"
                    _remove_created_ancestors(group)
                    continue
                # A target that shows neither state cannot be compensated safely
                if not matches(target, group.new_digest):
                    return False
                if group.old_existed:
                    recovery = Path(group.recovery_path or "")
                    if not matches(recovery, group.old_digest):
                        return False
                    stage = Path(group.staging_path)
                    copy_verified(recovery, stage, group.old_digest)
                    stage.replace(target)
                else:
                    target.replace(group.staging_path)
                if not group_old_matches(group):
                    return False
                group.state = "COMPENSATED"
                repository.save(journal)
                _remove_created_ancestors(group)
            journal.phase, journal.resolved, journal.result = "ROLLED_BACK", True, "ROLLED_BACK"
            repository.save(journal)
            return True
        except (OSError, RestoreStorageError, RestorePathError):
            # Any failure leaves the journal in place for the next inspection
            return False

    @staticmethod
    def _cleanup_artifacts(journal: RestoreJournal, recovery_root: Path) -> None:
        """Remove staging, recovery, and ancestor debris left by a restore."""
        # Remove staged copies and the operation recovery folder
        remove_staging(journal.groups)
        recovery = recovery_root.resolve(strict=False) / journal.operation_id
        if recovery.exists():
            shutil.rmtree(recovery)
        # Then prune stage directories and ancestors created for the restore
        for group in reversed(journal.groups):
            _remove_created_ancestors(group)
            remove_stage_tree(group)
        # Refuse to report success while temporary artifacts survive
        residue = [Path(group.staging_path) for group in journal.groups if Path(group.staging_path).exists()]
        if recovery.exists() or residue:
            raise RestoreStorageError("STORAGE_FAILURE", "Restore temporary artifacts could not be removed.")

    @staticmethod
    def _retire(repository: RestoreJournalRepository, journal: RestoreJournal) -> bool:
        """Retire the journal, returning whether the archive step succeeded."""
        try:
            repository.retire(journal)
            return True
        except (OSError, RestoreJournalError):
            return False


def _recovery_diagnostic(message: str) -> dict[str, object]:
    """Build the diagnostic payload describing why startup is blocked."""
    return {"code": "RECOVERY_REQUIRED", "message": message, "usable": False}


def _remove_created_ancestors(group: RestoreGroup) -> None:
    """Remove ancestor directories created for one restore group."""
    # Start with the deepest directory so each removal can succeed
    for value in reversed(group.created_ancestors):
        path = Path(value)
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            break

