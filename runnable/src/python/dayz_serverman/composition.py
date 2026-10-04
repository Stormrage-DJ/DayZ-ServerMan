"""Production composition root for the portable storage foundation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adapters.windows.diagnostics import WindowsPathDiagnostics
from .adapters.windows.process_tree import WindowsChildProbe
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
from .application.operations.store import OperationStore
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
from .repositories.migrations import MigrationStorage
from .repositories.legacy_backup_index import LegacyBackupIndexRepository
from .repositories.migration_journal import MigrationJournalRepository
from .repositories.migration_publication import MigrationPublication
from .repositories.mod_publication_journal import PublicationJournalRepository
from .repositories.mod_publication_recovery import PublicationRecovery
from .repositories.backups import BackupStorage
from .repositories.restore_journal import RestoreJournalRepository
from .repositories.restore_storage import RestoreStorage
from .repositories.schedules import ScheduleRepository
from .repositories.paths import PortablePaths
from .repositories.profiles import ProfileRepository
from .repositories.workshop_recovery import inspect_workshop_recovery
from .profile_provisioning_composition import build_profile_provisioning
from .lifecycle_composition import build_lifecycle
from .profile_restore_composition import build_profile_restore
from .update_check_composition import (
    build_update_check, build_update_check_scheduler, build_update_status,
)
from .workshop_composition import build_workshop


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
    coordinator: ApplicationCoordinator
    bridge: BridgeFacade
    host_bridge: BridgeFacade


def build_composition(packaged_root: Path | None = None) -> ApplicationComposition:
    """Create production services without consulting the working directory."""
    # Resolve the portable root and materialize the storage layout
    paths = (
        PortablePaths.from_root(packaged_root)
        if packaged_root is not None
        else PortablePaths.from_source(Path(__file__))
    )
    paths.create_layout()
    # Build the settings, diagnostics, and logging services
    settings_repository = VersionedJsonRepository(paths.manager_config)
    path_diagnostics = WindowsPathDiagnostics()
    settings = SettingsService(settings_repository, paths, path_diagnostics)
    logger = StructuredLogger(paths.logs / "manager.jsonl")
    # Track long-running operations with durable records
    operation_store = OperationStore(paths.operations)
    operations = OperationManager(operation_store, logger=logger)
    publication_journals = PublicationJournalRepository(paths.publication_journals)
    publication_records = publication_journals.records()
    # Block mutations while unresolved mod publication records exist
    if publication_records:
        current_settings = settings.load()
        if current_settings.dayz_root is None:
            operations.block_for_recovery("Mutations are blocked by unresolved mod publication.")
        else:
            publication_recovery = PublicationRecovery(publication_journals).inspect(
                Path(current_settings.dayz_root),
            )
            if publication_recovery["blocked"]:
                operations.block_for_recovery(
                    "Mutations are blocked by unresolved mod publication recovery."
                )
    # Block mutations after an interrupted SteamCMD update
    workshop_recovery = inspect_workshop_recovery(paths.operations, WindowsChildProbe())
    if workshop_recovery["blocked"]:
        operations.block_for_recovery(
            "Mutations are blocked by an interrupted SteamCMD update with an unknown result."
        )
    # Build the profile, preference, and backup services
    state_repository = VersionedJsonRepository(paths.state_file)
    profile_repository = ProfileRepository(paths.profiles)
    profiles = ProfileService(profile_repository, settings)
    profile_provisioning_coordinator = build_profile_provisioning(profiles, settings, operations, paths.operations)
    preferences = PreferenceCoordinator(
        VersionedJsonRepository(paths.ui_preferences), profiles,
    )
    backup_storage = BackupStorage()
    backups = BackupService(profiles, settings, backup_storage)
    backup_coordinator = BackupCoordinator(backups, operations)
    # Build the configuration and medical feature editors
    configuration = ConfigurationService(profiles, settings)
    configuration_coordinator = ConfigurationCoordinator(configuration, operations)
    mission_configuration = MissionConfigurationService(profiles, settings)
    mission_configuration_coordinator = MissionConfigurationCoordinator(mission_configuration, operations)
    medical_features = MedicalFeatureService(profiles, settings, paths.tweak_baselines)
    medical_feature_coordinator = MedicalFeatureCoordinator(medical_features, operations)
    # Build migration services with legacy backup indexing
    migration_storage = MigrationStorage(paths.migrations)
    legacy_backup_repository = LegacyBackupIndexRepository(
        paths.migrations / "legacy-backup-index.json",
    )
    migration_publication = MigrationPublication(
        paths.root, migration_storage,
        MigrationJournalRepository(paths.migrations / "publication-journals"),
        block_recovery=operations.block_for_recovery,
    )
    migrations = MigrationService(
        paths, settings, settings_repository, profiles, profile_repository,
        migration_storage, publisher=migration_publication,
        backup_index_repository=legacy_backup_repository,
    )
    migrations.inspect_recovery()
    migration_coordinator = MigrationCoordinator(migrations, operations)
    legacy_backups = LegacyBackupService(legacy_backup_repository)
    legacy_backup_coordinator = LegacyBackupCoordinator(legacy_backups, operations)
    # Build process control together with its independent readiness signal
    lifecycle, mutex = build_lifecycle(
        settings, profiles, state_repository, operations, paths.logs,
    )
    # Build restore services and gate mutations on pending recovery
    restore_journals = RestoreJournalRepository(paths.operations / "restore-journals")
    restores = RestoreService(
        profiles, settings, backup_storage, RestoreStorage(), restore_journals,
        paths.backup_recovery, lifecycle, mutex,
    )
    recovery = restores.inspect_recovery()
    if recovery["blocked"]:
        operations.block_for_recovery("Mutations are blocked by unresolved restore recovery.")
    restore_coordinator = RestoreCoordinator(restores, operations)
    profile_restore_coordinator = build_profile_restore(paths, profiles, settings, backup_storage, lifecycle, mutex, operations)
    # Build shutdown and schedule coordination
    shutdown = ShutdownCoordinator(operations, logger, lifecycle.shutdown_safe)
    lifecycle_coordinator = LifecycleCoordinator(lifecycle, operations, backups)
    schedules = ScheduleCoordinator(
        ScheduleRepository(VersionedJsonRepository(paths.schedules)),
        profiles, settings, preferences, lifecycle, lifecycle_coordinator, logger,
    )
    # Build the remote update check; it never runs on the operation lane
    update_check = build_update_check(paths, profiles, profile_repository, preferences, logger)
    update_check_scheduler = build_update_check_scheduler(update_check, preferences, logger)
    # Build SteamCMD update, inventory, and publication services
    workshop = build_workshop(
        paths, profiles, settings, operations, lifecycle, preferences, schedules,
        publication_journals, update_check, logger, backups,
    )
    update_check_coordinator = build_update_status(update_check, workshop.mod_inventory)
    # Assemble every coordinator's handlers into the bridge facade
    coordinator = ApplicationCoordinator(settings, operations, shutdown)
    logs = LogQueryService(paths.logs / "manager.jsonl", paths.logs / "dayz-server.log")
    handlers = {
        **coordinator.handlers(),
        **workshop.profile_coordinator.handlers(),
        **profile_provisioning_coordinator.handlers(),
        **preferences.handlers(),
        **backup_coordinator.handlers(),
        **restore_coordinator.handlers(),
        **profile_restore_coordinator.handlers(),
        **configuration_coordinator.handlers(),
        **mission_configuration_coordinator.handlers(),
        **medical_feature_coordinator.handlers(),
        **migration_coordinator.handlers(),
        **legacy_backup_coordinator.handlers(),
        **lifecycle_coordinator.handlers(),
        **schedules.handlers(),
        **workshop.workshop_coordinator.handlers(),
        **workshop.mod_inventory_coordinator.handlers(),
        **workshop.mod_publication_coordinator.handlers(),
        **workshop.mod_restart_coordinator.handlers(),
        **workshop.verification_coordinator.handlers(),
        **update_check_coordinator.handlers(),
        **logs.handlers(),
    }
    bridge = BridgeFacade(handlers, logger)
    # Return the frozen composition consumed by the host runtime
    return ApplicationComposition(
        paths=paths,
        settings=settings,
        settings_repository=settings_repository,
        path_diagnostics=path_diagnostics,
        state=state_repository,
        logger=logger,
        operations=operations,
        shutdown=shutdown,
        profile_repository=profile_repository,
        profiles=profiles,
        profile_coordinator=workshop.profile_coordinator,
        preferences=preferences,
        backups=backups,
        backup_coordinator=backup_coordinator,
        restores=restores,
        restore_coordinator=restore_coordinator,
        configuration=configuration,
        configuration_coordinator=configuration_coordinator,
        mission_configuration=mission_configuration,
        mission_configuration_coordinator=mission_configuration_coordinator,
        medical_features=medical_features,
        medical_feature_coordinator=medical_feature_coordinator,
        migrations=migrations,
        migration_coordinator=migration_coordinator,
        legacy_backups=legacy_backups,
        legacy_backup_coordinator=legacy_backup_coordinator,
        lifecycle=lifecycle,
        lifecycle_coordinator=lifecycle_coordinator,
        schedules=schedules,
        logs=logs,
        workshop_updates=workshop.workshop_updates,
        workshop_coordinator=workshop.workshop_coordinator,
        mod_inventory=workshop.mod_inventory,
        mod_inventory_coordinator=workshop.mod_inventory_coordinator,
        mod_publication=workshop.mod_publication,
        mod_publication_coordinator=workshop.mod_publication_coordinator,
        update_check=update_check,
        update_check_scheduler=update_check_scheduler,
        update_check_coordinator=update_check_coordinator,
        coordinator=coordinator,
        bridge=bridge,
        host_bridge=bridge,
    )
