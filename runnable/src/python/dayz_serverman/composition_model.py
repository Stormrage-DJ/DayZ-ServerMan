"""Immutable graph of the production services that one composition root builds."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .adapters.windows.diagnostics import WindowsPathDiagnostics
from .application.coordinator import ApplicationCoordinator
from .application.backup_coordinator import BackupCoordinator
from .application.backups import BackupService
from .application.configuration import ConfigurationService
from .application.configuration_coordinator import ConfigurationCoordinator
from .application.lifecycle_coordinator import LifecycleCoordinator
from .application.server_readiness import ReadinessLifecycleService
from .application.logs import LogQueryService
from .application.mission_configuration import MissionConfigurationService
from .application.mission_configuration_coordinator import MissionConfigurationCoordinator
from .application.medical_features import MedicalFeatureService
from .application.medical_feature_coordinator import MedicalFeatureCoordinator
from .application.migration_coordinator import MigrationCoordinator
from .application.mod_publication import ModPublicationService
from .application.mod_publication_coordinator import ModPublicationCoordinator
from .application.mod_inventory import ModInventoryService
from .application.mod_inventory_coordinator import ModInventoryCoordinator
from .application.migrations import MigrationService
from .application.legacy_backup_coordinator import LegacyBackupCoordinator
from .application.legacy_backups import LegacyBackupService
from .application.operations.manager import OperationManager
from .application.profile_coordinator import ProfileCoordinator
from .application.preferences import PreferenceCoordinator
from .application.profiles import ProfileService
from .application.restore_coordinator import RestoreCoordinator
from .application.restores import RestoreService
from .application.schedules import ScheduleCoordinator
from .application.settings import SettingsService
from .application.shutdown import ShutdownCoordinator
from .application.update_check import UpdateCheckService
from .application.update_check_coordinator import UpdateCheckCoordinator
from .application.update_check_scheduler import UpdateCheckScheduler
from .application.workshop_coordinator import WorkshopCoordinator
from .application.workshop_updates import WorkshopUpdateService
from .bridge.facade import BridgeFacade
from .observability.structured_log import StructuredLogger
from .repositories.json_store import VersionedJsonRepository
from .repositories.paths import PortablePaths
from .repositories.profiles import ProfileRepository
from .update_check_composition import ServerBuildParts


class SessionMode(str, Enum):
    """Kind of session that a composition serves (A4)."""

    # GUI and writing commands: under the instance lock, with every recovery
    OWNER = "OWNER"
    # Read-only commands: no lock, no recovery, no write of any kind
    OBSERVER = "OBSERVER"


@dataclass(frozen=True)
class ApplicationComposition:
    """Immutable graph of production services for one manager root."""
    paths: PortablePaths
    settings: SettingsService
    settings_repository: VersionedJsonRepository
    path_diagnostics: WindowsPathDiagnostics
    state: VersionedJsonRepository
    logger: StructuredLogger
    operations: OperationManager
    shutdown: ShutdownCoordinator
    profile_repository: ProfileRepository
    profiles: ProfileService
    profile_coordinator: ProfileCoordinator
    preferences: PreferenceCoordinator
    backups: BackupService
    backup_coordinator: BackupCoordinator
    restores: RestoreService
    restore_coordinator: RestoreCoordinator
    configuration: ConfigurationService
    configuration_coordinator: ConfigurationCoordinator
    mission_configuration: MissionConfigurationService
    mission_configuration_coordinator: MissionConfigurationCoordinator
    medical_features: MedicalFeatureService
    medical_feature_coordinator: MedicalFeatureCoordinator
    migrations: MigrationService
    migration_coordinator: MigrationCoordinator
    legacy_backups: LegacyBackupService
    legacy_backup_coordinator: LegacyBackupCoordinator
    lifecycle: ReadinessLifecycleService
    lifecycle_coordinator: LifecycleCoordinator
    schedules: ScheduleCoordinator
    logs: LogQueryService
    workshop_updates: WorkshopUpdateService
    workshop_coordinator: WorkshopCoordinator
    mod_inventory: ModInventoryService
    mod_inventory_coordinator: ModInventoryCoordinator
    mod_publication: ModPublicationService
    mod_publication_coordinator: ModPublicationCoordinator
    update_check: UpdateCheckService
    update_check_scheduler: UpdateCheckScheduler
    update_check_coordinator: UpdateCheckCoordinator
    server_build: ServerBuildParts
    coordinator: ApplicationCoordinator
    bridge: BridgeFacade
    host_bridge: BridgeFacade
    mode: SessionMode = SessionMode.OWNER
