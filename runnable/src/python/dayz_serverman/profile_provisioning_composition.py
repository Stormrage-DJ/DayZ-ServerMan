"""Focused composition for guided profile provisioning."""

from __future__ import annotations

from pathlib import Path

from .application.mission_catalog import MissionCatalog
from .application.operations.manager import OperationManager
from .application.profile_provisioning import ProfileProvisioningError, ProfileProvisioningService
from .application.profile_provisioning_coordinator import ProfileProvisioningCoordinator
from .application.profiles import ProfileService
from .application.settings import SettingsService
from .repositories.provisioning_journal import ProvisioningJournalRepository


def build_profile_provisioning(
    profiles: ProfileService,
    settings: SettingsService,
    operations: OperationManager,
    operations_root: Path,
) -> ProfileProvisioningCoordinator:
    """Build provisioning services and inspect interrupted work at startup."""
    service = ProfileProvisioningService(
        profiles,
        settings,
        MissionCatalog(),
        ProvisioningJournalRepository(operations_root / "profile-provisioning"),
    )
    try:
        service.recover()
    except ProfileProvisioningError as error:
        operations.block_for_recovery(str(error))
    return ProfileProvisioningCoordinator(service, operations)
