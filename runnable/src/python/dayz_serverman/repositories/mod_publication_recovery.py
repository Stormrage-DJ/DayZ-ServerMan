"""Whole-set classification and recovery for mod publication."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    resolve_artifacts,
    safe_dayz_root,
)
from ..domain.mod_publication import (
    GroupState,
    PublicationJournal,
    PublicationPhase,
)
from .backup_verification import is_reparse
from .mod_publication_inventory import PublicationInventoryError, inventory_tree
from .mod_publication_journal import PublicationJournalError, PublicationJournalRepository


class LiveState(str, Enum):
    """Classification of one publication target against its recorded proofs."""
    PRIOR = "PRIOR"
    OUTPUT = "OUTPUT"
    UNCHANGED = "UNCHANGED"
    PRIOR_MOVED = "PRIOR_MOVED"
    THIRD_STATE = "THIRD_STATE"


def classify_all(journal: PublicationJournal, dayz_root: Path) -> tuple[LiveState, ...]:
    """Classify the on-disk state of every target group in the journal."""
    # Validate the DayZ root once before touching any target
    root = safe_dayz_root(dayz_root)
    return tuple(_classify(group, root) for group in journal.groups)


class PublicationRecovery:
    """Inspect and repair journals whose publication was interrupted."""

    def __init__(
        self,
        repository: PublicationJournalRepository,
        fault_hook: Callable[[str, int], None] | None = None,
        checkpoint: Callable[[str, int], None] | None = None,
    ) -> None:
        """Store the journal repository and optional fault-injection hooks."""
        self.repository = repository
        self._fault = fault_hook or (lambda _phase, _index: None)
        self._checkpoint = checkpoint or (lambda _phase, _index: None)

    def inspect(self, dayz_root: Path) -> dict[str, object]:
        """Classify every stored journal and report blocked state with diagnostics."""
        blocked = False
        diagnostics: list[dict[str, str]] = []
        for path, journal in self.repository.records():
            if journal is None:
                blocked = True
                diagnostics.append(_diagnostic("Publication journal is invalid."))
                continue
            try:
                # Verify the authority record before classifying live targets
                self.repository.verify_authority(journal)
                states = classify_all(journal, dayz_root)
                # A prepared journal whose targets are all prior can be dropped
                if journal.phase == PublicationPhase.PREPARED and all(
                    state in (LiveState.PRIOR, LiveState.UNCHANGED) for state in states
                ):
                    _cleanup(journal, dayz_root)
                    self._fault("AFTER_CLEANUP", -1)
                    path.unlink()
                    continue
                # A committed journal whose targets are all output is finalized
                if journal.phase == PublicationPhase.COMMITTED and all(
                    state in (LiveState.OUTPUT, LiveState.UNCHANGED) for state in states
                ):
                    # Promote unchanged groups to verified output before saving
                    for group in journal.groups:
                        if group.state != GroupState.UNCHANGED_VERIFIED:
                            group.state = GroupState.OUTPUT_VERIFIED
                    journal.phase = PublicationPhase.COMMITTED
                    journal.publication_started = journal.committed = True
                    journal.resolved = False
                    journal.result = None
                    self.repository.save(journal)
                    _cleanup(journal, dayz_root)
                    self._fault("AFTER_CLEANUP", -1)
                    journal.resolved = True
                    journal.result = "COMMITTED"
                    self.repository.save(journal)
                    self.repository.retire(journal)
                    continue
                # A rolled back journal whose targets are all prior is finalized
                if journal.phase == PublicationPhase.ROLLED_BACK and all(
                    state in (LiveState.PRIOR, LiveState.UNCHANGED) for state in states
                ):
                    _cleanup(journal, dayz_root)
                    journal.resolved = True
                    journal.result = "ROLLED_BACK"
                    self.repository.save(journal)
                    self.repository.retire(journal)
                    continue
                # Otherwise compensate when no target sits in a third state
                if LiveState.THIRD_STATE not in states and self.compensate(journal, dayz_root):
                    _cleanup(journal, dayz_root)
                    self._fault("AFTER_CLEANUP", -1)
                    journal.resolved = True
                    journal.result = "ROLLED_BACK"
                    self.repository.save(journal)
                    self.repository.retire(journal)
                    continue
            except (OSError, PublicationPathError, PublicationInventoryError, PublicationJournalError):
                pass
            # Any failure leaves the journal blocked for the operator
            blocked = True
            if journal.phase not in (PublicationPhase.COMMITTED, PublicationPhase.ROLLED_BACK):
                _mark_recovery_required(journal, self.repository)
            diagnostics.append(_diagnostic("Publication state could not be proven."))
        # Report the aggregate block flag with per-journal diagnostics
        return {"blocked": blocked, "diagnostics": diagnostics}

    def compensate(self, journal: PublicationJournal, dayz_root: Path) -> bool:
        """Roll every published target back to its prior state when provably safe."""
        try:
            # An unverifiable authority or third state forces manual recovery
            self.repository.verify_authority(journal)
            root = safe_dayz_root(dayz_root)
            self._fault("BEFORE_COMPENSATION_CLASSIFY", -1)
            states = classify_all(journal, root)
            if LiveState.THIRD_STATE in states or not _recovery_artifacts_valid(
                journal, root, states,
            ):
                _mark_recovery_required(journal, self.repository)
                return False
            # Mark the journal as compensating before touching any target
            journal.phase = PublicationPhase.COMPENSATING
            journal.committed = journal.resolved = False
            journal.result = None
            self.repository.save(journal)
            self._fault("AFTER_COMPENSATING_SAVE", -1)
            # Restore targets in reverse order so dependencies unwind safely
            for index in range(len(journal.groups) - 1, -1, -1):
                group = journal.groups[index]
                if group.state == GroupState.UNCHANGED_VERIFIED:
                    continue
                if states[index] == LiveState.PRIOR:
                    continue
                self._checkpoint("COMPENSATE_TARGET", index)
                target, stage, recovery = resolve_artifacts(
                    root, group.target_relative, group.stage_name, group.recovery_name,
                )
                if stage.exists() and target.exists():
                    return _recovery_failure(journal, self.repository)
                # Move the published target aside, then restore the prior copy
                if target.exists():
                    target.replace(stage)
                if group.prior_existed:
                    if not recovery.is_dir() or inventory_tree(recovery) != group.prior_digest:
                        return _recovery_failure(journal, self.repository)
                    recovery.replace(target)
                group.state = GroupState.PRIOR_RESTORED
                self.repository.save(journal)
                self._fault("AFTER_COMPENSATION_TARGET_SAVE", index)
            # After all moves, targets must classify back to prior or unchanged
            if any(state not in (LiveState.PRIOR, LiveState.UNCHANGED)
                   for state in classify_all(journal, root)):
                return _recovery_failure(journal, self.repository)
            # Record the rolled back outcome for the caller to retire
            journal.phase = PublicationPhase.ROLLED_BACK
            journal.resolved = False
            journal.result = None
            self.repository.save(journal)
            self._fault("AFTER_ROLLED_BACK_SAVE", -1)
            return True
        except (OSError, PublicationPathError, PublicationInventoryError, PublicationJournalError):
            return _recovery_failure(journal, self.repository)


def _classify(group: object, root: Path) -> LiveState:
    """Classify one group by comparing live artifacts with its proofs."""
    target, stage, recovery = resolve_artifacts(
        root, group.target_relative, group.stage_name, group.recovery_name,
    )
    # Unchanged groups must still match their verified output digest
    if group.state == GroupState.UNCHANGED_VERIFIED:
        if (stage.exists() or recovery.exists() or not target.is_dir()
                or inventory_tree(target) != group.output_digest):
            return LiveState.THIRD_STATE
        return LiveState.UNCHANGED
    # Missing targets are prior state unless a prior move is proven
    if not target.exists():
        if not group.prior_existed:
            return LiveState.PRIOR
        if (
            group.state == GroupState.PRIOR_MOVED and recovery.is_dir()
            and inventory_tree(recovery) == group.prior_digest
        ):
            return LiveState.PRIOR_MOVED
        return LiveState.THIRD_STATE
    # A non-directory target can never match a recorded directory digest
    if not target.is_dir():
        return LiveState.THIRD_STATE
    digest = inventory_tree(target)
    if group.state in (
        GroupState.OUTPUT_PUBLISHED, GroupState.OUTPUT_VERIFIED,
    ) and digest == group.output_digest:
        return LiveState.OUTPUT
    if group.prior_existed and digest == group.prior_digest:
        return LiveState.PRIOR
    if digest == group.output_digest:
        return LiveState.OUTPUT
    return LiveState.THIRD_STATE


def _cleanup(journal: PublicationJournal, dayz_root: Path) -> None:
    """Remove staging and recovery artifacts for every group in the journal."""
    root = safe_dayz_root(dayz_root)
    for group in journal.groups:
        _, stage, recovery = resolve_artifacts(
            root, group.target_relative, group.stage_name, group.recovery_name,
        )
        for artifact in (stage, recovery):
            if artifact.exists():
                # Only real directories may be deleted; links are refused
                if not artifact.is_dir() or is_reparse(artifact):
                    raise PublicationPathError("publication artifact is unsafe")
                shutil.rmtree(artifact)
            # Deletion must succeed; a surviving artifact aborts cleanup
            if artifact.exists():
                raise PublicationPathError("publication artifact cleanup failed")


def _recovery_artifacts_valid(
    journal: PublicationJournal,
    root: Path,
    states: tuple[LiveState, ...],
) -> bool:
    """Return whether recovery artifacts prove that compensation is safe."""
    for group, state in zip(journal.groups, states):
        if state == LiveState.UNCHANGED:
            continue
        if state not in (LiveState.OUTPUT, LiveState.PRIOR_MOVED) or not group.prior_existed:
            continue
        try:
            _, _, recovery = resolve_artifacts(
                root, group.target_relative, group.stage_name, group.recovery_name,
            )
            if not recovery.is_dir() or inventory_tree(recovery) != group.prior_digest:
                return False
        except (OSError, PublicationPathError, PublicationInventoryError):
            return False
    return True


def _mark_recovery_required(
    journal: PublicationJournal, repository: PublicationJournalRepository,
) -> None:
    """Persist the recovery-required phase, tolerating save failures."""
    journal.phase = PublicationPhase.RECOVERY_REQUIRED
    journal.publication_started = True
    journal.committed = journal.resolved = False
    journal.result = "RECOVERY_REQUIRED"
    try:
        repository.save(journal)
    except Exception:
        pass


def _recovery_failure(
    journal: PublicationJournal, repository: PublicationJournalRepository,
) -> bool:
    """Mark the journal recovery-required and return the failure result."""
    _mark_recovery_required(journal, repository)
    return False


def _diagnostic(message: str) -> dict[str, str]:
    """Build one recovery diagnostic entry."""
    return {"code": "RECOVERY_REQUIRED", "message": message}
