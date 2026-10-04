"""Compose direct restoration separately from the existing-profile restore graph."""

from .adapters.windows.udp_ports import bound_udp_ports
from .application.installation_guard import InstallationGuard
from .application.profile_restores import ProfileRestoreService
from .application.profile_restore_coordinator import ProfileRestoreCoordinator
from .application.startup_recoveries import recover_interrupted_profile_restores
from .repositories.profile_restore_storage import ProfileRestoreStorage
from .repositories.profile_restore_journal import ProfileRestoreJournal


def build_profile_restore(paths, profiles, settings, backups, lifecycle, mutex, operations):
    """Recover joint journals before exposing handlers or allowing queued mutations."""
    storage = ProfileRestoreStorage(ProfileRestoreJournal(paths.operations / "profile-restore-journals"), paths.profiles, paths.backup_recovery)
    # Recovery writes into the DayZ root, so it runs under the mutex with a proven stopped server
    recover_interrupted_profile_restores(storage, settings, operations, InstallationGuard(lifecycle, mutex))
    service = ProfileRestoreService(profiles, settings, backups, storage, lifecycle, mutex, bound_udp_ports)
    return ProfileRestoreCoordinator(service, operations)
