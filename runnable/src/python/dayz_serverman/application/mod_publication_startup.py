"""Startup recovery of an interrupted mod publication, inside the write guard of a publication.

Recovery renames mod folders back and removes stage and recovery folders, so it
writes into the DayZ root. Like every such write it needs the installation
mutex and a proven stopped server (customer decision D10).
"""

from __future__ import annotations

from pathlib import Path

from ..domain.lifecycle import LifecycleFailure
from ..repositories.mod_publication_journal import PublicationJournalRepository
from ..repositories.mod_publication_recovery import PublicationRecovery
from .installation_guard import InstallationGuard
from .operations.manager import OperationManager
from .settings import SettingsService


def recover_interrupted_publications(
    journals: PublicationJournalRepository, settings: SettingsService,
    operations: OperationManager, guard: InstallationGuard,
) -> None:
    """Finish or undo interrupted publications; block mutations when that is not possible now.

    Without an unresolved journal nothing is read and nothing is written. Each
    block reason is logged by the operation lane.
    """
    if not journals.records():
        return
    current = settings.load()
    if current.dayz_root is None:
        operations.block_for_recovery("Mutations are blocked by unresolved mod publication.")
        return
    root = Path(current.dayz_root)
    try:
        # The guard refuses before the block runs, so a refusal writes nothing
        with guard.stopped(root):
            blocked = PublicationRecovery(journals).inspect(root)["blocked"]
    except LifecycleFailure:
        # A busy installation, or a server that is not proven stopped: recover at a later start
        operations.block_for_recovery(
            "Mutations are blocked by an interrupted mod publication while the server is not proven stopped."
        )
        return
    if blocked:
        operations.block_for_recovery(
            "Mutations are blocked by unresolved mod publication recovery."
        )
