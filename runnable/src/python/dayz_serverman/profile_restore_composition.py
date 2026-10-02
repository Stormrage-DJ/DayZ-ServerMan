"""Compose direct restoration separately from the existing-profile restore graph."""

from pathlib import Path

from .adapters.windows.udp_ports import bound_udp_ports
from .application.profile_restores import ProfileRestoreService
from .application.profile_restore_coordinator import ProfileRestoreCoordinator
from .repositories.profile_restore_storage import ProfileRestoreStorage
from .repositories.profile_restore_journal import ProfileRestoreJournal
from .domain.lifecycle import ServerState


def build_profile_restore(paths, profiles, settings, backups, lifecycle, mutex, operations):
    """Recover joint journals before exposing handlers or allowing queued mutations."""
    storage = ProfileRestoreStorage(ProfileRestoreJournal(paths.operations / "profile-restore-journals"), paths.profiles, paths.backup_recovery)
    current = settings.load()
    try:
        records = storage.journals.records()
        pending = any(record["phase"] not in {"COMMITTED", "ROLLED_BACK"} or (record["phase"] == "COMMITTED" and not record["cleanup_complete"]) for record in records)
        blocked = False
        if pending:
            if current.dayz_root is None or lifecycle.status().state != ServerState.STOPPED:
                blocked = True
            else:
                root = Path(current.dayz_root).resolve(strict=True)
                with mutex.guard(str(root)):
                    blocked = storage.inspect(root)["blocked"]
    except Exception:
        blocked = True
    if blocked:
        operations.block_for_recovery("Direct profile restore recovery requires attention.")
    service = ProfileRestoreService(profiles, settings, backups, storage, lifecycle, mutex, bound_udp_ports)
    return ProfileRestoreCoordinator(service, operations)
