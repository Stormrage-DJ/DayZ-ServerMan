"""Assemble the bridge handler table from the coordinators of one composition."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .application.server_readiness import ReadinessLifecycleService
from .bridge.facade import BridgeHandler
from .lifecycle_composition import build_online_players


# Read handlers that an observer session holds (4.3); S5 measured that they write only log lines
OBSERVER_READ_METHODS = frozenset((
    "get_application_snapshot", "get_server_status", "get_online_players", "get_lifecycle_schedule",
    "get_update_status", "list_mod_inventory", "list_backups", "list_backup_catalog", "list_profiles",
    "read_profile", "preview_profile_command", "list_profile_missions", "get_ui_preferences",
    "load_configuration", "load_mission_configuration", "load_medical_features", "read_log",
    "validate_settings_path_selection", "select_legacy_root", "preview_legacy_import", "inspect_backup_archive",
))
# Observer reads that open nothing inside a folder that an owner step renames or removes (3.4, QF-2)
READER_EXEMPT_METHODS = frozenset((
    "read_log", "list_backups", "list_backup_catalog", "get_application_snapshot",
    "get_lifecycle_schedule", "get_ui_preferences", "list_profiles", "read_profile",
))
# Every other method of the owner allowlist: lane operations, synchronous writes, mutex, SteamCMD, cache
OWNER_ONLY_METHODS = frozenset((
    "get_operation", "read_operation_events", "request_operation_cancellation", "request_shutdown",
    "save_settings", "save_profile", "delete_profile", "provision_profile", "save_selected_profile",
    "save_backup_after_stop", "save_automatic_update_checks", "create_backup", "preview_restore",
    "apply_restore", "inspect_restore_recovery", "preview_profile_restore", "restore_profile_from_backup",
    "preview_configuration", "apply_configuration", "preview_mission_configuration",
    "apply_mission_configuration", "convert_starter_loadout", "preview_medical_feature",
    "apply_medical_feature", "apply_legacy_import", "list_legacy_backup_references",
    "revalidate_legacy_backup_references", "start_server", "stop_server", "restart_server",
    "save_lifecycle_schedule", "save_steam_settings", "authenticate_steamcmd", "update_workshop_items",
    "preview_mod_publication", "publish_mods_and_keys", "apply_mods_and_restart", "verify_mod_files",
    "request_update_check",
))


def observer_handlers(handlers: Mapping[str, BridgeHandler]) -> dict[str, BridgeHandler]:
    """Return only the read handlers of an observer session, in handler-table order."""
    return {name: handler for name, handler in handlers.items() if name in OBSERVER_READ_METHODS}


class HandlerSource(Protocol):
    """A coordinator that names its bridge handlers."""

    def handlers(self) -> Mapping[str, BridgeHandler]:
        """Return the bridge methods of this coordinator with their handlers."""
        ...


def build_handler_table(
    *,
    coordinator: HandlerSource,
    profile_coordinator: HandlerSource,
    profile_provisioning_coordinator: HandlerSource,
    preferences: HandlerSource,
    backup_coordinator: HandlerSource,
    restore_coordinator: HandlerSource,
    profile_restore_coordinator: HandlerSource,
    configuration_coordinator: HandlerSource,
    mission_configuration_coordinator: HandlerSource,
    medical_feature_coordinator: HandlerSource,
    migration_coordinator: HandlerSource,
    legacy_backup_coordinator: HandlerSource,
    lifecycle_coordinator: HandlerSource,
    lifecycle: ReadinessLifecycleService,
    schedules: HandlerSource,
    workshop_coordinator: HandlerSource,
    mod_inventory_coordinator: HandlerSource,
    mod_publication_coordinator: HandlerSource,
    mod_restart_handlers: Mapping[str, BridgeHandler],
    verification_coordinator: HandlerSource,
    update_check_coordinator: HandlerSource,
    logs: HandlerSource,
) -> dict[str, BridgeHandler]:
    """Return every bridge handler of the composition, keyed by bridge method name."""
    # Keep the entry order of the composition root, so the bridge allowlist does not change
    return {
        **coordinator.handlers(),
        **profile_coordinator.handlers(),
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
        # D18: the names of the players online, read only while the names panel is open
        **build_online_players(lifecycle).handlers(),
        **schedules.handlers(),
        **workshop_coordinator.handlers(),
        **mod_inventory_coordinator.handlers(),
        **mod_publication_coordinator.handlers(),
        # "Update & restart" arrives already wrapped by the composition root with the D11 check
        **mod_restart_handlers,
        **verification_coordinator.handlers(),
        **update_check_coordinator.handlers(),
        **logs.handlers(),
    }
