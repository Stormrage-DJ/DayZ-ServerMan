"""Production composition root for the portable storage foundation."""

from __future__ import annotations

from pathlib import Path

from .adapters.windows.diagnostics import WindowsPathDiagnostics
from .adapters.windows.process_tree import WindowsChildProbe
from .application.coordinator import ApplicationCoordinator
from .application.backup_coordinator import BackupCoordinator
from .application.backups import BackupService
from .application.configuration import ConfigurationService
from .application.configuration_coordinator import ConfigurationCoordinator
from .application.installation_guard import InstallationGuard
from .application.lifecycle_ownership import LaunchOwnership
from .application.lifecycle_ports import ServerFolderWriterPort
from .application.lifecycle_coordinator import LifecycleCoordinator, for_running_profile
from .application.logs import LogQueryService
from .application.mission_configuration import MissionConfigurationService
from .application.mission_configuration_coordinator import MissionConfigurationCoordinator
from .application.medical_features import MedicalFeatureService
from .application.medical_feature_coordinator import MedicalFeatureCoordinator
from .application.operations.manager import OperationManager
from .application.operations.store import OperationStore
from .application.preferences import PreferenceCoordinator
from .application.profiles import ProfileService
from .application.restore_coordinator import RestoreCoordinator
from .application.restores import RestoreService
from .application.schedules import ScheduleCoordinator
from .application.settings import SettingsService
from .application.shutdown import ShutdownCoordinator
from .application.startup_recoveries import recover_interrupted_restores
from .bridge.facade import BridgeFacade
from .observability.structured_log import NullStructuredLogger, StructuredLogger
from .repositories.json_store import StagingPolicy, VersionedJsonRepository
from .repositories.mod_publication_journal import PublicationJournalRepository
from .repositories.backups import BackupStorage
from .repositories.restore_journal import RestoreJournalRepository
from .repositories.restore_storage import RestoreStorage
from .repositories.schedules import ScheduleRepository
from .repositories.paths import PortablePaths
from .repositories.profiles import ProfileRepository
from .repositories.server_ownership import OWNERSHIP_FILE, ServerOwnershipRepository
from .repositories.workshop_recovery import inspect_workshop_recovery
from .bridge_composition import build_handler_table, observer_handlers
from .composition_model import ApplicationComposition, SessionMode
from .profile_provisioning_composition import build_profile_provisioning
from .lifecycle_composition import build_lifecycle
from .migration_composition import build_migration
from .mission_map_composition import build_mission_map
from .profile_restore_composition import build_profile_restore, build_settings_repair
from .update_check_composition import (
    build_server_build, build_update_check, build_update_check_scheduler, build_update_status,
)
from .workshop_composition import build_workshop
from .adapters.windows.steamcmd import SteamCmdConsole


def build_composition(
    packaged_root: Path | None = None, *, mode: SessionMode = SessionMode.OWNER,
    folder_writer: ServerFolderWriterPort | None = None,
    sign_in: SteamCmdConsole = SteamCmdConsole.NEW_CONSOLE,
) -> ApplicationComposition:
    """Create production services without consulting the working directory.

    The defaults are the GUI's owner session. An observer session (A4) creates no
    folder, writes no log line, runs no recovery, refuses every submit and holds
    only the read handlers. `folder_writer` is the A13 writer side of an owner;
    `sign_in` is the console of the Steam sign-in (a command shares its own, 10.5).
    """
    observer = mode is SessionMode.OBSERVER
    staging = StagingPolicy.OBSERVER if observer else StagingPolicy.OWNER
    # Resolve the portable root and materialize the storage layout; only an owner creates it
    paths = (
        PortablePaths.from_root(packaged_root)
        if packaged_root is not None
        else PortablePaths.from_source(Path(__file__))
    )
    if not observer:
        paths.create_layout()
    # Build the settings, diagnostics, and logging services
    settings_repository = VersionedJsonRepository(paths.manager_config, staging=staging)
    path_diagnostics = WindowsPathDiagnostics()
    settings = SettingsService(settings_repository, paths, path_diagnostics)
    logger = NullStructuredLogger() if observer else StructuredLogger(paths.logs / "manager.jsonl")
    # Track long-running operations with durable records; an observer's lane refuses every submit
    operation_store = OperationStore(paths.operations, create_root=not observer)
    operations = OperationManager(operation_store, logger=logger)
    if observer:
        operations.begin_shutdown()
    # An interrupted mod publication is recovered by the workshop composition, inside the write guard
    publication_journals = PublicationJournalRepository(paths.publication_journals)
    # Block mutations after an interrupted SteamCMD update; an observer only reports pending recoveries
    if not observer and inspect_workshop_recovery(paths.operations, WindowsChildProbe())["blocked"]:
        operations.block_for_recovery(
            "Mutations are blocked by an interrupted SteamCMD update with an unknown result."
        )
    # Build the profile, preference, and backup services
    state_repository = VersionedJsonRepository(paths.state_file, staging=staging)
    profile_repository = ProfileRepository(paths.profiles, staging=staging)
    profiles = ProfileService(profile_repository, settings)
    preferences = PreferenceCoordinator(
        VersionedJsonRepository(paths.ui_preferences, staging=staging), profiles,
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
    # Mission map plans: one association lock set for plan saves and the later commit step (T2.3-F9)
    mission_map = build_mission_map(paths, profiles, settings, staging=staging)
    # Build migration services with legacy backup indexing; only an owner inspects an interrupted publication
    migration = build_migration(
        paths, settings, settings_repository, profiles, profile_repository, operations,
        staging=staging, recover=not observer,
    )
    # A6: the ownership record lets a session adopt a server that another session of this root started;
    # only an owner writes it
    ownership = LaunchOwnership(
        ServerOwnershipRepository(paths.data / OWNERSHIP_FILE, staging=staging), paths.root,
        logger=logger, writable=not observer, session_id=operations.session_id,
    )
    # Build process control together with its independent readiness signal
    lifecycle, mutex = build_lifecycle(
        settings, profiles, state_repository, operations, paths.logs, preferences, ownership=ownership,
    )
    # Recoveries that write into the DayZ root take the mutex and need a proven stopped server
    installation_guard = InstallationGuard(lifecycle, mutex)
    profile_provisioning_coordinator = build_profile_provisioning(
        profiles, settings, operations, paths.operations, installation_guard, recover=not observer,
    )
    # Build restore services and gate mutations on pending recovery
    restore_journals = RestoreJournalRepository(paths.operations / "restore-journals", create_root=not observer)
    restores = RestoreService(
        profiles, settings, backup_storage, RestoreStorage(), restore_journals,
        paths.backup_recovery, lifecycle, mutex, folder_writer,
    )
    if not observer:
        recover_interrupted_restores(restore_journals, restores, operations)
    restore_coordinator = RestoreCoordinator(restores, operations)
    profile_restore_coordinator = build_profile_restore(
        paths, profiles, settings, backup_storage, lifecycle, mutex, operations,
        folder_writer=folder_writer, recover=not observer,
    )
    # Build shutdown and schedule coordination
    shutdown = ShutdownCoordinator(operations, logger, lifecycle.shutdown_safe)
    lifecycle_coordinator = LifecycleCoordinator(lifecycle, operations, backups)
    schedules = ScheduleCoordinator(
        ScheduleRepository(VersionedJsonRepository(paths.schedules, staging=staging)),
        profiles, settings, preferences, lifecycle, lifecycle_coordinator, logger,
    )
    # Build the remote update check; it never runs on the operation lane
    update_check = build_update_check(paths, profiles, profile_repository, preferences, logger)
    update_check_scheduler = build_update_check_scheduler(update_check, preferences, logger)
    # The server build check shares one SteamCMD run guard with the Workshop update and sign-in
    server_build = build_server_build(
        paths, settings, operations, preferences, shutdown, update_check_scheduler, logger,
    )
    # Build SteamCMD update, inventory, and publication services
    workshop = build_workshop(
        paths, profiles, settings, operations, lifecycle, preferences, schedules,
        publication_journals, update_check, logger, backups, steamcmd_guard=server_build.guard,
        folder_writer=folder_writer, recover=not observer, sign_in=sign_in,
    )
    update_check_coordinator = build_update_status(
        update_check, workshop.mod_inventory, server_build.service,
    )
    # Assemble every coordinator's handlers into the bridge facade
    coordinator = ApplicationCoordinator(settings, operations, shutdown, build_settings_repair(paths, settings, restore_journals))
    logs = LogQueryService(paths.logs / "manager.jsonl", paths.logs / "dayz-server.log")
    # "Update & restart" stops the running server, so it must name the running profile (D11)
    mod_restart_handlers = {name: for_running_profile(lifecycle, handler)
                            for name, handler in workshop.mod_restart_coordinator.handlers().items()}
    handlers = build_handler_table(
        coordinator=coordinator,
        profile_coordinator=workshop.profile_coordinator,
        profile_provisioning_coordinator=profile_provisioning_coordinator,
        preferences=preferences,
        backup_coordinator=backup_coordinator,
        restore_coordinator=restore_coordinator,
        profile_restore_coordinator=profile_restore_coordinator,
        configuration_coordinator=configuration_coordinator,
        mission_configuration_coordinator=mission_configuration_coordinator,
        medical_feature_coordinator=medical_feature_coordinator,
        migration_coordinator=migration.migration_coordinator,
        legacy_backup_coordinator=migration.legacy_backup_coordinator,
        lifecycle_coordinator=lifecycle_coordinator,
        lifecycle=lifecycle,
        schedules=schedules,
        workshop_coordinator=workshop.workshop_coordinator,
        mod_inventory_coordinator=workshop.mod_inventory_coordinator,
        mod_publication_coordinator=workshop.mod_publication_coordinator,
        mod_restart_handlers=mod_restart_handlers,
        verification_coordinator=workshop.verification_coordinator,
        update_check_coordinator=update_check_coordinator,
        logs=logs,
    )
    # An observer's facade holds only the read handlers, so no write can be reached (4.3)
    bridge = BridgeFacade(observer_handlers(handlers) if observer else handlers, logger)
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
        migrations=migration.migrations,
        migration_coordinator=migration.migration_coordinator,
        legacy_backups=migration.legacy_backups,
        legacy_backup_coordinator=migration.legacy_backup_coordinator,
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
        server_build=server_build,
        mission_map=mission_map,
        coordinator=coordinator,
        bridge=bridge,
        host_bridge=bridge,
        mode=mode,
    )
