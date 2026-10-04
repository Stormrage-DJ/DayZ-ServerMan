"""Destination-sibling staging and transactional directory publication."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    dayz_root_identity,
    resolve_artifacts,
    safe_dayz_root,
    safe_target,
)
from ..domain.mod_publication import (
    GroupState,
    PublicationGroup,
    PublicationIntent,
    PublicationJournal,
    PublicationPhase,
    TargetRole,
    publication_fingerprint,
    validate_intent,
)
from .backup_verification import is_reparse
from .mod_publication_inventory import PublicationInventoryError, inventory_tree
from .mod_publication_journal import PublicationJournalRepository
from .mod_publication_recovery import PublicationRecovery
from .mod_publication_staging import (
    StagingError, measured_fingerprint, stage_keys_group, stage_mod_group,
)


class PublicationStorageError(RuntimeError):
    """Raised when publication staging or execution cannot proceed safely."""

    def __init__(self, code: str, message: str, *, recovery_required: bool = False) -> None:
        """Store the error code and whether recovery is required."""
        self.code = code
        self.recovery_required = recovery_required
        super().__init__(message)


class PublicationCancelled(RuntimeError):
    """A cooperative cancellation request at a declared checkpoint."""


class ModPublicationStorage:
    """Stage and publish mod directories with journaled recovery."""

    def __init__(
        self,
        fault_hook: Callable[[str, int], None] | None = None,
        checkpoint: Callable[[str, int], None] | None = None,
    ) -> None:
        """Store optional fault and checkpoint observers for tests."""
        # Default observers keep checkpoint reporting optional
        self._fault = fault_hook or (lambda _phase, _index: None)
        self._checkpoint = checkpoint or (lambda _phase, _index: None)
        # Fingerprint of each copied target, measured directly before its post-commit hash
        self.hashed_fingerprints: dict[str, str | None] = {}

    def stage(
        self, intent: PublicationIntent, dayz_root: Path, *, writes_allowed: bool = True,
    ) -> PublicationJournal:
        """Validate an intent and stage every group beside its live target.

        With `writes_allowed` false the run is read-only in the DayZ root: a
        group that needs a copy ends the staging as PUBLICATION_PREVIEW_STALE.
        """
        validate_intent(intent)
        # An unsigned intent cannot be tied to a preview
        if not intent.fingerprint:
            raise PublicationStorageError("PUBLICATION_PREVIEW_STALE", "publication intent is unsigned")
        # Resolve the root once and reuse it for every group
        root = safe_dayz_root(dayz_root)
        self._require_root(intent, root)
        # Collect groups in publication order so the journal stays deterministic
        groups: list[PublicationGroup] = []
        try:
            # Stage each managed mod directory beside its live target
            for source in intent.managed_sources:
                target = self._prepare_target(intent, root, source.target_relative, len(groups))
                # A run without the write guard must not stage a copy
                if not writes_allowed and not source.target_current:
                    raise StagingError("PUBLICATION_PREVIEW_STALE: managed target needs a copy")
                groups.append(stage_mod_group(
                    operation=intent.publication_id, ordinal=len(groups),
                    relative=source.target_relative, source=Path(source.source_path),
                    target=target, expected_digest=source.output_digest,
                    expected_source_metadata=source.cache_proof.metadata_inventory_digest,
                    expected_target_metadata=source.cache_proof.target_metadata_digest,
                    target_current=source.target_current,
                    checkpoint=self._checkpoint, fault=self._fault,
                ))
            # Stage server keys as the final group when requested
            if intent.keys:
                target = self._prepare_target(intent, root, "keys", len(groups))
                groups.append(stage_keys_group(
                    intent=intent, ordinal=len(groups), target=target,
                    checkpoint=self._checkpoint, fault=self._fault,
                    writes_allowed=writes_allowed,
                ))
            # Assemble the prepared journal with its publication fingerprint
            journal = PublicationJournal(
                intent.publication_id, intent.fingerprint, PublicationPhase.PREPARED,
                False, False, False, None, groups,
            )
            journal.publication_fingerprint = publication_fingerprint(intent.fingerprint, groups)
            journal.authority_intent = intent
            self._fault("AFTER_ALL_STAGED", len(groups))
            return journal
        except StagingError as error:
            # Remove partial artifacts before reporting the staging failure
            self._cleanup_groups(groups, root)
            code, separator, message = str(error).partition(": ")
            raise PublicationStorageError(
                code if separator else "PUBLICATION_STAGE_FAILED",
                message if separator else str(error),
            ) from error
        except Exception:
            # Unexpected failures also leave no partial artifacts behind
            self._cleanup_groups(groups, root)
            raise

    def publish(
        self,
        journal: PublicationJournal,
        dayz_root: Path,
        repository: PublicationJournalRepository,
    ) -> None:
        """Publish staged groups and compensate on failure."""
        root = safe_dayz_root(dayz_root)
        retired = False
        try:
            # Persist the prepared journal before any live mutation
            repository.bind_authority(journal, journal.authority_intent)
            repository.save(journal)
            self._fault("AFTER_PREPARED_JOURNAL", -1)
            self._checkpoint("BEFORE_PUBLICATION", -1)
            # Mark publishing before the first live move
            journal.phase = PublicationPhase.PUBLISHING
            journal.publication_started = True
            repository.save(journal)
            self._fault("AFTER_PUBLISHING_SAVE", -1)
            # Publish each changed group in recorded order
            for index, group in enumerate(journal.groups):
                if group.state == GroupState.UNCHANGED_VERIFIED:
                    continue
                # Resolve the sibling stage and recovery paths for this group
                target, stage, recovery = resolve_artifacts(
                    root, group.target_relative, group.stage_name, group.recovery_name,
                )
                self._fault("BEFORE_PRIOR_MOVE", index)
                # Move the prior target aside so rollback can restore it
                if group.prior_existed:
                    target.replace(recovery)
                    group.state = GroupState.PRIOR_MOVED
                    repository.save(journal)
                    self._fault("AFTER_PRIOR_MOVED_SAVE", index)
                self._fault("BEFORE_OUTPUT_MOVE", index)
                stage.replace(target)
                group.state = GroupState.OUTPUT_PUBLISHED
                repository.save(journal)
                self._fault("AFTER_OUTPUT_PUBLISHED_SAVE", index)
                self._fault("BEFORE_OUTPUT_VERIFY", index)
                # Verify the live target against the staged digest
                if inventory_tree(target) != group.output_digest:
                    raise PublicationStorageError(
                        "PUBLICATION_VERIFICATION_FAILED", "published target changed",
                    )
                group.state = GroupState.OUTPUT_VERIFIED
                repository.save(journal)
                self._fault("AFTER_OUTPUT_VERIFIED", index)
                self._checkpoint("AFTER_LIVE_TARGET", index)
            # Commit only after every changed group verified
            journal.phase = PublicationPhase.COMMITTED
            journal.committed = True
            repository.save(journal)
            self._fault("AFTER_COMMITTED_SAVE", -1)
            # Re-verify all changed targets after the commit marker; the fingerprint that
            # is measured first lets a proof record be tied to this hash later
            for group in journal.groups:
                if group.state == GroupState.UNCHANGED_VERIFIED:
                    continue
                target = resolve_artifacts(
                    root, group.target_relative, group.stage_name, group.recovery_name)[0]
                self.hashed_fingerprints[group.target_relative] = measured_fingerprint(target)
                if inventory_tree(target) != group.output_digest:
                    raise PublicationStorageError(
                        "PUBLICATION_VERIFICATION_FAILED", "committed target changed",
                        recovery_required=True,
                    )
            # Remove stage and recovery artifacts after the commit proof
            self._cleanup_groups(journal.groups, root)
            self._fault("AFTER_CLEANUP", -1)
            # Record the terminal result and retire the journal
            journal.resolved = True
            journal.result = "COMMITTED"
            repository.save(journal)
            repository.retire(journal)
            retired = True
            self._fault("AFTER_RETIREMENT", -1)
        except Exception as error:
            if retired:
                return
            # Distinguish cooperative cancellation from hard failures
            cancelled = isinstance(error, PublicationCancelled)
            # Prepublication failures only need staged evidence cleaned up
            if not journal.publication_started:
                self._cleanup_groups(journal.groups, root)
                try:
                    repository.discard_prepared(journal)
                except Exception as cleanup_error:
                    raise PublicationStorageError(
                        "RECOVERY_REQUIRED", "prepublication evidence could not be cleaned",
                        recovery_required=True,
                    ) from cleanup_error
                raise PublicationStorageError(
                    "UPDATE_CANCELLED" if cancelled else "PUBLICATION_STAGE_FAILED",
                    "publication stopped before live mutation",
                ) from error
            # A committed journal must never roll back; recovery is required
            if journal.phase == PublicationPhase.COMMITTED:
                raise PublicationStorageError(
                    "RECOVERY_REQUIRED", "committed publication could not be finalized",
                    recovery_required=True,
                ) from error
            # Attempt compensation from the persisted journal state
            recovered = PublicationRecovery(
                repository, fault_hook=self._fault, checkpoint=self._checkpoint,
            ).compensate(journal, root)
            # A proven rollback becomes the reported outcome
            if recovered:
                self._cleanup_groups(journal.groups, root)
                self._fault("AFTER_CLEANUP", -1)
                journal.resolved = True
                journal.result = "ROLLED_BACK"
                repository.save(journal)
                repository.retire(journal)
                try:
                    self._fault("AFTER_RETIREMENT", -1)
                except Exception:
                    pass
                raise PublicationStorageError(
                    "UPDATE_CANCELLED" if cancelled else "PUBLICATION_VERIFICATION_FAILED",
                    "publication failed and was rolled back",
                ) from error
            # Unproven state blocks further mutations
            raise PublicationStorageError(
                "RECOVERY_REQUIRED", "publication failed and prior state is not proven",
                recovery_required=True,
            ) from error

    @staticmethod
    def _cleanup_groups(groups: list[PublicationGroup], root: Path) -> None:
        """Remove stage and recovery artifacts for every group that is safe to touch."""
        for group in groups:
            # Resolve paths defensively so cleanup never masks the real failure
            try:
                _, stage, recovery = resolve_artifacts(
                    root, group.target_relative, group.stage_name, group.recovery_name,
                )
            except PublicationPathError:
                continue
            # Remove both artifacts, refusing unsafe directory shapes
            for path in (stage, recovery):
                if path.exists():
                    if not path.is_dir() or is_reparse(path):
                        raise PublicationStorageError(
                            "RECOVERY_REQUIRED", "publication artifact is unsafe",
                            recovery_required=True,
                        )
                    shutil.rmtree(path)
                if path.exists():
                    raise PublicationStorageError(
                        "RECOVERY_REQUIRED", "publication artifact cleanup failed",
                        recovery_required=True,
                    )

    def _prepare_target(
        self, intent: PublicationIntent, root: Path, relative: str, ordinal: int,
    ) -> Path:
        """Checkpoint, re-check the root, and resolve one staging target."""
        self._checkpoint("STAGE_TARGET", ordinal)
        self._fault("BEFORE_STAGE", ordinal)
        # Re-verify the DayZ root after checkpoints may have changed it
        self._checkpoint("CHECK_TARGET", ordinal)
        self._require_root(intent, root)
        return safe_target(root, relative)

    @staticmethod
    def _require_root(intent: PublicationIntent, root: Path) -> None:
        """Refuse a DayZ root whose identity differs from the intent."""
        if dayz_root_identity(root) != intent.dayz_root_identity:
            raise PublicationStorageError("PUBLICATION_PREVIEW_STALE", "DayZ root changed")
