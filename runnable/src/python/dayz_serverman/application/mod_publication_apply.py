"""Write decision and guarded apply of a reviewed publication."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from ..adapters.windows.publication_paths import (
    PublicationPathError,
    safe_dayz_root,
    safe_target,
)
from ..domain.lifecycle import LifecycleFailure
from ..domain.mod_publication import PublicationIntent, PublicationJournal
from ..repositories.mod_publication_inventory import PublicationInventoryError
from ..repositories.mod_publication_journal import (
    PublicationJournalError,
    PublicationJournalRepository,
)
from ..repositories.mod_publication_stage import (
    ModPublicationStorage,
    PublicationCancelled,
    PublicationStorageError,
)
from ..repositories.mod_publication_staging import StagingError, missing_key_count
from .installation_guard import InstallationGuard
from .mod_publication_start import ModPublicationError, empty_publication_journal
from .operations.models import OperationCancelled


def missing_keys(intent: PublicationIntent, dayz_root: Path) -> int:
    """Return how many key files of the plan are not in the keys folder; read-only.

    A keys folder that cannot be read counts every key as missing. The plan is
    then treated as writing, and staging reports the real error.
    """
    try:
        return missing_key_count(intent, safe_target(safe_dayz_root(dayz_root), "keys"))
    except (OSError, PublicationPathError, StagingError):
        return len(intent.keys)


def publication_writes(intent: PublicationIntent, dayz_root: Path) -> bool:
    """Return whether the publication would write into the DayZ root; read-only.

    A source that is not current counts as writing even when a hash would find
    its folder equal, because only staging can prove that.
    """
    return (any(not source.target_current for source in intent.managed_sources)
            or missing_keys(intent, dayz_root) > 0)


def apply_publication(
    *, storage: ModPublicationStorage, journals: PublicationJournalRepository,
    intent: PublicationIntent, dayz_root: Path, start_requested: bool,
    guard: InstallationGuard | None, guard_plain_apply: bool,
    record_proofs: Callable[[PublicationJournal], None],
) -> PublicationJournal:
    """Stage and publish the reviewed targets, inside the write guard when one is required.

    `record_proofs` receives the committed journal, which names the copied groups.
    The guard is held through staging, the live replace and the proof records;
    it is released when this function returns, before any pre-start check.
    """
    # Decide before staging whether this run writes into the DayZ root
    writes = publication_writes(intent, dayz_root)
    # The one use of the plain-apply policy: a start request always needs the guard
    guarded = guard is not None and writes and (start_requested or guard_plain_apply)
    try:
        with guard.stopped(dayz_root) if guarded else nullcontext():
            if intent.managed_sources or intent.keys:
                # A run that decided not to write must not copy anything later
                journal = (storage.stage(intent, dayz_root) if writes
                           else storage.stage(intent, dayz_root, writes_allowed=False))
                storage.publish(journal, dayz_root, journals)
            else:
                journal = empty_publication_journal(intent)
            # Record the content proofs best effort; publication already succeeded
            record_proofs(journal)
        return journal
    except LifecycleFailure as error:
        # The guard refused: nothing was staged and nothing changed
        raise ModPublicationError(error.code, error.safe_message) from error
    except PublicationCancelled as error:
        raise OperationCancelled(str(error)) from error
    except PublicationJournalError as error:
        raise ModPublicationError(
            "RECOVERY_REQUIRED", "Publication journal verification failed.",
            recovery_required=True,
        ) from error
    except PublicationPathError as error:
        raise ModPublicationError("PATH_INVALID", str(error)) from error
    except OSError as error:
        raise ModPublicationError("PUBLICATION_FAILED", "Publication storage failed.") from error
    except (PublicationInventoryError, PublicationStorageError) as error:
        raise ModPublicationError(
            getattr(error, "code", "PUBLICATION_FAILED"), str(error),
            recovery_required=getattr(error, "recovery_required", False),
        ) from error
