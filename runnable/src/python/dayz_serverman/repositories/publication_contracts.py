"""Contracts of the shared publication engine: journal and group protocols, policy, hooks, new-state test."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from .backup_verification import is_reparse
from .restore_paths import matches


class PublicationGroup(Protocol):
    """The fields of one per-file group that the engine reads; it writes only the state."""

    target_path: str
    staging_path: str
    recovery_path: str | None
    old_existed: bool
    old_digest: str | None
    state: str
    created_ancestors: tuple[str, ...]

    @property
    def new_digest(self) -> str | None:
        """Return the digest of the new file; None only when the file must end absent."""


class PublicationJournal(Protocol):
    """The fields of one journal that the engine reads and advances."""

    operation_id: str
    phase: str
    publication_started: bool
    resolved: bool
    result: str | None

    @property
    def committed(self) -> bool:
        """Return whether the journal reached its committed terminal state."""

    @property
    def groups(self) -> Sequence[PublicationGroup]:
        """Return the per-file groups in publication order."""


# One journal kind; a store, its hooks and the engine calls all use the same kind
JournalT = TypeVar("JournalT", bound=PublicationJournal)


class PublicationJournalStore(Protocol[JournalT]):
    """Durable store of journals whose groups have the backup-restore group shape."""

    def save(self, journal: JournalT) -> object:
        """Persist the current journal state."""

    def retire(self, journal: JournalT) -> object:
        """Archive a resolved journal."""

    def records(self) -> Iterable[tuple[Path, JournalT | None]]:
        """Return each stored journal path with its journal, or None when unreadable."""


@dataclass(frozen=True)
class PublicationPolicy:
    """Error types, operator messages and checks that one journal kind gives the shared engine."""

    # Raised as error_type(code, message) for cleanup failures; caught during compensation
    error_type: type[Exception]
    # Raised by the journal store when a journal cannot be retired
    journal_error_type: type[Exception]
    # Message when staging or recovery artifacts survive cleanup
    cleanup_message: str
    # Diagnostic when a journal is unreadable or its paths are unsafe
    invalid_message: str
    # Diagnostic when an interrupted journal proves neither its prior nor its new state
    unresolved_message: str
    # Text of the OSError raised when a published target fails verification
    verify_message: str
    # D4 step 5: check the old state of each group again just before its replace; off for backup restore
    recheck_old_state: bool = False
    # Message of error_type("TARGET_CHANGED", message) when that check finds a changed target
    changed_message: str = ""


@dataclass(frozen=True)
class RecoveryHooks(Generic[JournalT]):
    """Caller steps of a journal kind whose records publish after its groups (D4); backup restore has none."""

    # At COMMITTING with every group target proven new, and at a committed journal not yet retired: publish
    # each staged record not yet published (an equal manifest digest counts) and mark it PUBLISHED; False blocks
    roll_forward: Callable[[JournalT], bool]
    # After a rollback, and at a rolled-back journal not yet retired: discard the staged records; False blocks
    rollback: Callable[[JournalT], bool]


def new_exists(group: PublicationGroup) -> bool:
    """Return whether the group publishes a file; only an editor group can require absence (D4)."""
    # Backup-restore groups have no new_exists field and always publish a file
    return bool(getattr(group, "new_exists", True))


def in_new_state(group: PublicationGroup) -> bool:
    """Return whether the target has the new digest, or is absent when the group requires absence."""
    target = Path(group.target_path)
    if new_exists(group):
        return matches(target, group.new_digest)
    # A dangling link is not an absent file
    return not target.exists() and not is_reparse(target)
