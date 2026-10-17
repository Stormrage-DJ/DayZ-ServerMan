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
    "load_configuration", "load_mission_configuration", "load_medical_features", "load_mission_map", "read_log",
    "validate_settings_path_selection", "select_legacy_root", "preview_legacy_import", "inspect_backup_archive",
    "inspect_mission_map", "continue_mission_map_inspection", "cancel_mission_map_inspection",
    # The export of the saved map plan returns text and takes no path (D16, DC-7); the CLI writes the file
    "export_mission_map_plan",
))
# Observer reads that open nothing inside a folder that an owner step renames or removes (3.4, QF-2)
READER_EXEMPT_METHODS = frozenset((
    "read_log", "list_backups", "list_backup_catalog", "get_application_snapshot",
    "get_lifecycle_schedule", "get_ui_preferences", "list_profiles", "read_profile",
    "cancel_mission_map_inspection",
))
# Observer reads that take the reader side themselves, once per mission-read slice (A181); the session
# wrapper does not hold the reader for the whole call
SLICED_READER_METHODS = frozenset(("inspect_mission_map", "continue_mission_map_inspection"))
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
    "request_update_check", "save_mission_map_plan",
    # Set aside of invalid saved plan data (5.3, A11): an owner write, a confirming command in task 10
    "set_aside_mission_map_plan",
    # Display reads of the map editor (3.17, A11): owner methods, never observer reads
    "read_mission_map_tier_layer", "read_mission_map_background",
    # Both previews, Apply, the restores and the editor recovery (8.9, A11): owner methods, never observer reads
    "preview_mission_map", "apply_mission_map", "preview_mission_map_restore", "restore_mission_map",
    "inspect_mission_map_recovery",
    # The import preview and the import (10.33, AR-1): owner methods, the review step and the save of map import
    "preview_mission_map_import", "import_mission_map_plan",
    # The copy preview and the copy (10.7): owner methods of map copy, as import
    "preview_mission_map_copy", "copy_mission_map_plan",
    # The adoption preview (10.13, AR-1) and the adopt step in the lane (10.35): owner methods of map adopt
    "preview_mission_map_adoption", "adopt_mission_map",
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
    mission_map_coordinator: HandlerSource,
    mission_map_display: HandlerSource,
    mission_map_publication: HandlerSource,
    mission_map_transfer: HandlerSource,
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
        **mission_map_coordinator.handlers(),
        # The tier-layer read (`tier`) and the background read (`selection_id`, `part`) of 3.15 and 3.16;
        # both are permanent parity exclusions, and `over_budget` refusals carry `limit`, `size` and `unit`
        **mission_map_display.handlers(),
        # Apply preview and Apply, restore preview and restore, and the on-demand editor recovery (8.9)
        **mission_map_publication.handlers(),
        # The transfer methods of task 10: export (10.6), import (10.33) and copy (10.7), then the adoption
        # preview (10.13)
        **mission_map_transfer.handlers(),
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
