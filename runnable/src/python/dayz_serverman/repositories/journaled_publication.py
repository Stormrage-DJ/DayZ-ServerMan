"""Journaled per-file publication, reverse compensation, and interrupted-journal inspection."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from ..adapters.windows.shared_files import replace_file
# PublicationPolicy stays importable from this module, as restore_storage imports it here
from .publication_contracts import (
    JournalT,
    PublicationGroup,
    PublicationJournal,
    PublicationJournalStore,
    PublicationPolicy,
    RecoveryHooks,
    in_new_state,
    new_exists,
)
from .restore_paths import (
    RestorePathError,
    copy_verified,
    group_old_matches,
    matches,
    old_state_matches,
    remove_staging,
    remove_stage_tree,
)


class JournaledPublication:
    """Publish, compensate, and inspect journaled per-file groups in a target root."""
    def __init__(self, fault_hook: Callable[[str, int], None], policy: PublicationPolicy) -> None:
        """Store the fault-injection hook and the policy of the owning journal kind."""
        self._fault_hook = fault_hook
        self._policy = policy

    def publish_groups(self, journal: JournalT, repository: PublicationJournalStore[JournalT]) -> None:
        """Publish every group in order, journaling each state change; the caller ends the phase sequence."""
        # Mark the journal as publishing before the first file moves
        journal.phase, journal.publication_started = "PUBLISHING", True
        repository.save(journal)
        for index, group in enumerate(journal.groups):
            group.state = "PUBLISHING"
            repository.save(journal)
            self._fault_hook("BEFORE_PUBLISH", index)
            # A target that changed after staging is never replaced; the raise leads to compensation
            if self._policy.recheck_old_state and not group_old_matches(group):
                raise self._policy.error_type("TARGET_CHANGED", self._policy.changed_message)
            if new_exists(group):
                self._move_in(group, index)
            else:
                self._move_aside(group, index)
            group.state = "PUBLISHED"
            repository.save(journal)

    def _move_in(self, group: PublicationGroup, index: int) -> None:
        """Move the staged file over the target and verify the published bytes."""
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
            raise OSError(self._policy.verify_message)

    def _move_aside(self, group: PublicationGroup, index: int) -> None:
        """Make the target absent: prove its recovery copy, move it to the staging path, then prove absence."""
        # The old bytes must exist in a verified recovery copy before the target leaves
        if group.old_existed and not matches(Path(group.recovery_path or ""), group.old_digest):
            raise OSError(self._policy.verify_message)
        target = Path(group.target_path)
        if target.exists():
            replace_file(target, group.staging_path)
        self._fault_hook("AFTER_PUBLISH", index)
        if not in_new_state(group):
            raise OSError(self._policy.verify_message)

    def compensate(self, journal: JournalT, repository: PublicationJournalStore[JournalT]) -> bool:
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
                if not in_new_state(group):
                    return False
                if group.old_existed:
                    recovery = Path(group.recovery_path or "")
                    if not matches(recovery, group.old_digest):
                        return False
                    stage = Path(group.staging_path)
                    # A target moved aside sits at its staging path; the verified recovery copy replaces it
                    if not new_exists(group) and stage.is_file():
                        stage.unlink()
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
        except (OSError, self._policy.error_type, RestorePathError):
            # Any failure leaves the journal in place for the next inspection
            return False

    def inspect(
        self,
        repository: PublicationJournalStore[JournalT],
        recovery_root: Path,
        paths_safe: Callable[[JournalT], bool],
        hooks: RecoveryHooks[JournalT] | None = None,
    ) -> dict[str, object]:
        """Resolve interrupted journals and report whether any still block.

        paths_safe re-derives every path of one journal and returns False when
        any of them no longer maps to its expected location. Without hooks, a
        COMMITTING journal cannot occur, and resolved journals are only retired.
        """
        diagnostics: list[dict[str, object]] = []
        blocked = False
        # Inspect unresolved journals before already-resolved ones
        records = sorted(
            repository.records(),
            key=lambda item: (item[1] is None, item[1].resolved if item[1] is not None else False),
        )
        for path, journal in records:
            # A journal with unsafe paths can never be acted on automatically
            if journal is None or not paths_safe(journal):
                blocked = True
                diagnostics.append(_recovery_diagnostic(self._policy.invalid_message))
                continue
            # Past the commit point only a roll-forward is allowed, and only from a proven new state
            if hooks is not None and journal.phase == "COMMITTING":
                safe = self._roll_forward(journal, repository, recovery_root, hooks)
            # A committed journal only needs its targets proven and archived
            elif journal.committed and journal.phase == "COMMITTED":
                safe = all(in_new_state(group) for group in journal.groups)
                if safe and hooks is not None:
                    safe = self._settle(journal, recovery_root, hooks.roll_forward)
                if safe:
                    safe = self._retire(repository, journal)
            # A rolled-back journal only needs its prior state proven and archived
            elif journal.resolved and journal.phase == "ROLLED_BACK":
                safe = old_state_matches(journal.groups)
                if safe and hooks is not None:
                    safe = self._settle(journal, recovery_root, hooks.rollback)
                if safe:
                    safe = self._retire(repository, journal)
            # An unpublished journal that still holds the prior state is closed out
            elif not journal.publication_started and old_state_matches(journal.groups):
                journal.phase, journal.resolved, journal.result = "ROLLED_BACK", True, "ROLLED_BACK"
                repository.save(journal)
                self.cleanup(journal, recovery_root)
                safe = (hooks is None or hooks.rollback(journal)) and self._retire(repository, journal)
            else:
                # Otherwise compensate the interrupted publication in place
                safe = self.compensate(journal, repository)
                if safe:
                    self.cleanup(journal, recovery_root)
                    safe = (hooks is None or hooks.rollback(journal)) and self._retire(repository, journal)
            if not safe:
                blocked = True
                diagnostics.append(_recovery_diagnostic(self._policy.unresolved_message))
        return {"blocked": blocked, "diagnostics": diagnostics}

    def _roll_forward(
        self, journal: JournalT, repository: PublicationJournalStore[JournalT], recovery_root: Path,
        hooks: RecoveryHooks[JournalT],
    ) -> bool:
        """Finish a COMMITTING journal when every group target is new; otherwise block without a write."""
        if not all(in_new_state(group) for group in journal.groups) or not hooks.roll_forward(journal):
            return False
        # Every record is published now, so the journal reaches its terminal state
        journal.phase, journal.resolved, journal.result = "COMMITTED", True, "COMMITTED"
        try:
            repository.save(journal)
        except (OSError, self._policy.journal_error_type):
            # The store refuses the state when a record is still unpublished; the journal stays at COMMITTING
            return False
        return self._settle(journal, recovery_root, None) and self._retire(repository, journal)

    def _settle(self, journal: JournalT, recovery_root: Path, hook: Callable[[JournalT], bool] | None) -> bool:
        """Finish the cleanup of a resolved journal and run the caller's record step; False blocks."""
        try:
            self.cleanup(journal, recovery_root)
            return hook is None or hook(journal)
        except (OSError, self._policy.error_type, RestorePathError):
            return False

    def cleanup(self, journal: PublicationJournal, recovery_root: Path) -> None:
        """Remove staging, recovery, and ancestor debris left by a publication."""
        # Remove staged copies and the operation recovery folder
        remove_staging(journal.groups)
        recovery = recovery_root.resolve(strict=False) / journal.operation_id
        if recovery.exists():
            shutil.rmtree(recovery)
        # Then prune stage directories and ancestors created for the publication
        for group in reversed(journal.groups):
            _remove_created_ancestors(group)
            remove_stage_tree(group)
        # Refuse to report success while temporary artifacts survive
        residue = [Path(group.staging_path) for group in journal.groups if Path(group.staging_path).exists()]
        if recovery.exists() or residue:
            raise self._policy.error_type("STORAGE_FAILURE", self._policy.cleanup_message)

    def _retire(self, repository: PublicationJournalStore[JournalT], journal: JournalT) -> bool:
        """Retire the journal, returning whether the archive step succeeded."""
        try:
            repository.retire(journal)
            return True
        except (OSError, self._policy.journal_error_type):
            return False


def _recovery_diagnostic(message: str) -> dict[str, object]:
    """Build the diagnostic payload describing why startup is blocked."""
    return {"code": "RECOVERY_REQUIRED", "message": message, "usable": False}


def _remove_created_ancestors(group: PublicationGroup) -> None:
    """Remove ancestor directories created for one published group."""
    # Start with the deepest directory so each removal can succeed
    for value in reversed(group.created_ancestors):
        path = Path(value)
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            break
