"""Verified staging and journaled restore of backup snapshots into the DayZ root."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain.backups import BackupManifest
from ..domain.restores import RestoreJournal, RestoreTarget
from .backup_verification import sha256_file
from .journaled_publication import JournaledPublication, PublicationPolicy
from .restore_journal import RestoreJournalError, RestoreJournalRepository
from .restore_preparation import prepare_groups
from .restore_paths import (
    copy_verified,
    journal_paths_safe,
    matches,
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


# Error types and operator messages of backup restore for the shared publication engine
_RESTORE_POLICY = PublicationPolicy(
    error_type=RestoreStorageError,
    journal_error_type=RestoreJournalError,
    cleanup_message="Restore temporary artifacts could not be removed.",
    invalid_message="A restore journal is invalid or unsafe.",
    unresolved_message="An interrupted restore could not prove the prior or restored state.",
    verify_message="published restore target failed verification",
)


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
        # Delegate per-file publication, compensation, and inspection to the shared engine
        self._engine = JournaledPublication(self._fault_hook, _RESTORE_POLICY)

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
                self._engine.cleanup(journal, recovery_root)
                raise
            if self._engine.compensate(journal, journal_repository):
                self._engine.cleanup(journal, recovery_root)
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

    def _publish(self, journal: RestoreJournal, journal_repository: RestoreJournalRepository) -> None:
        """Publish every group through the engine, then record the committed state as backup restore always has."""
        self._engine.publish_groups(journal, journal_repository)
        # Record the terminal committed state
        journal.phase, journal.committed, journal.resolved, journal.result = (
            "COMMITTED", True, True, "COMMITTED",
        )
        journal_repository.save(journal)

    def inspect(
        self,
        journal_repository: RestoreJournalRepository,
        dayz_root: Path,
        recovery_root: Path,
    ) -> dict[str, object]:
        """Resolve interrupted journals and report whether any still block."""
        # Each journal path must still derive from the DayZ root and the recovery folder
        return self._engine.inspect(
            journal_repository,
            recovery_root,
            lambda journal: journal_paths_safe(journal, dayz_root, recovery_root),
        )
