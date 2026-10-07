"""Startup recoveries that write into the DayZ installation, inside the installation guard.

An interrupted backup restore, direct profile restore or profile creation is
finished or undone when the manager starts. Each writes into the DayZ root, so,
like the recovery of an interrupted mod publication (QF-026), it needs the
installation mutex and a proven stopped server (customer decision D14, QF-027).
A refusal writes nothing, blocks mutations with a logged reason and keeps the
journal, so the next manager start tries again.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.lifecycle import LifecycleFailure
from ..repositories.profile_restore_storage import ProfileRestoreStorage
from ..repositories.provisioning_journal import ProvisioningJournalRepository
from ..repositories.restore_journal import RestoreJournalRepository
from .folder_writer_scope import STARTUP_RECOVERY_WAIT_SECONDS
from .installation_guard import InstallationGuard
from .operations.manager import OperationManager
from .profile_provisioning import ProfileProvisioningError, ProfileProvisioningService
from .restores import RECOVERY_NO_DAYZ_ROOT, RECOVERY_NOT_STOPPED, RESTORE_KIND, RestoreService
from .settings import SettingsService


def recover_interrupted_restores(
    journals: RestoreJournalRepository, restores: RestoreService, operations: OperationManager,
) -> None:
    """Finish or undo an interrupted backup restore; block mutations when that is not possible now.

    The restore service holds the installation guard itself, because the Backups
    page runs the same recovery again on demand (QF-039).
    """
    # Without a journal nothing is read and nothing is written
    if not journals.records():
        return
    # The A13 writer side waits up to 30 s for observer reads; a refusal takes the block path below
    recovery = restores.inspect_recovery(wait_seconds=STARTUP_RECOVERY_WAIT_SECONDS)
    if recovery.get("reason") == RECOVERY_NOT_STOPPED:
        # A busy installation, or a server that is not proven stopped: recover at a later start
        operations.block_for_recovery(RECOVERY_NOT_STOPPED, owner=RESTORE_KIND)
    elif recovery.get("reason") == RECOVERY_NO_DAYZ_ROOT:
        # No DayZ server folder: a save that sets only that folder may pass this block (QF-069)
        operations.block_for_missing_dayz_root(RECOVERY_NO_DAYZ_ROOT, owner=RESTORE_KIND)
    elif recovery["blocked"]:
        # No readable DayZ folder, or a state that recovery cannot prove
        operations.block_for_recovery("Mutations are blocked by unresolved restore recovery.", owner=RESTORE_KIND)


def recover_interrupted_provisioning(
    journals: ProvisioningJournalRepository, service: ProfileProvisioningService,
    settings: SettingsService, operations: OperationManager, guard: InstallationGuard,
) -> None:
    """Remove the folders of an interrupted profile creation; block mutations when that is not possible now."""
    # An invalid journal raises here, as it did inside the recovery before the guard
    records = journals.list()
    if not records:
        return
    dayz_root = settings.load().dayz_root
    try:
        if dayz_root is None:
            # Without a configured DayZ folder no managed server can run: recover as before D14 (QF-044)
            service.recover()
            return
        # The guard of the configured installation; the recovery itself is unchanged by D14
        with guard.stopped(Path(dayz_root)):
            service.recover()
    except LifecycleFailure:
        operations.block_for_recovery(
            "Mutations are blocked by an interrupted profile creation while the server is not proven stopped."
        )
    except ProfileProvisioningError as error:
        operations.block_for_recovery(str(error))


def recover_interrupted_profile_restores(
    storage: ProfileRestoreStorage, settings: SettingsService,
    operations: OperationManager, guard: InstallationGuard,
) -> None:
    """Finish or undo an interrupted direct profile restore; block mutations when that is not possible now."""
    try:
        records = storage.journals.records()
        pending = any(
            record["phase"] not in {"COMMITTED", "ROLLED_BACK"}
            or (record["phase"] == "COMMITTED" and not record["cleanup_complete"])
            for record in records
        )
    except Exception:
        # A journal set that cannot be read is never acted on
        pending, records = True, None
    if not pending:
        return
    # Each cause has its own reason, so the operator sentence names a way out that fits it (QF-075)
    if records is None:
        operations.block_for_recovery("Direct profile restore journals could not be read.")
        return
    dayz_root = settings.load().dayz_root
    if dayz_root is None:
        # A save that sets only the DayZ server folder may pass this block (QF-069)
        operations.block_for_missing_dayz_root("Direct profile restore recovery requires a configured DayZ root.")
        return
    blocked = True
    try:
        root = Path(dayz_root).resolve(strict=True)
        # The state is read under the mutex, then the A13 writer side is taken; a refusal writes nothing
        with guard.stopped(root, folder_wait=STARTUP_RECOVERY_WAIT_SECONDS):
            blocked = storage.inspect(root)["blocked"]
    except LifecycleFailure:
        operations.block_for_recovery(
            "Mutations are blocked by an interrupted direct profile restore while the server is not proven stopped."
        )
        return
    except Exception:
        blocked = True
    if blocked:
        operations.block_for_recovery("Direct profile restore recovery requires attention.")
