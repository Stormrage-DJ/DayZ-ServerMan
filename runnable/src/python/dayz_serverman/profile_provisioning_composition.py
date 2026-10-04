"""Focused composition for guided profile provisioning."""

from __future__ import annotations

from pathlib import Path

from .application.installation_guard import InstallationGuard
from .application.mission_catalog import MissionCatalog
from .application.operations.manager import OperationManager
from .application.profile_provisioning import ProfileProvisioningService
from .application.profile_provisioning_coordinator import ProfileProvisioningCoordinator
from .application.profiles import ProfileService
from .application.settings import SettingsService
from .application.startup_recoveries import recover_interrupted_provisioning
from .repositories.provisioning_journal import ProvisioningJournalRepository


def build_profile_provisioning(
    profiles: ProfileService,
    settings: SettingsService,
    operations: OperationManager,
    operations_root: Path,
    guard: InstallationGuard,
) -> ProfileProvisioningCoordinator:
    """Build provisioning services and recover interrupted work at startup, inside the guard."""
    journals = ProvisioningJournalRepository(operations_root / "profile-provisioning")
    service = ProfileProvisioningService(profiles, settings, MissionCatalog(), journals)
    # Recovery removes folders in the DayZ root, so it needs the mutex and a proven stopped server
    recover_interrupted_provisioning(journals, service, settings, operations, guard)
    return ProfileProvisioningCoordinator(service, operations)
