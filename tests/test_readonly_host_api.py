"""Host API surface and strict bridge request construction tests."""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.bridge.contracts import ErrorCode  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError, BridgeFacade  # noqa: E402
from dayz_serverman.host.api import HostApi  # noqa: E402


class RecordingFacade:
    """Bridge facade double that records every dispatched request."""
    def __init__(self) -> None:
        """Start with an empty request log."""
        self.requests: list[dict] = []

    def dispatch(self, request: dict) -> dict:
        """Record the request and return a successful placeholder envelope."""
        self.requests.append(request)
        return {"success": True, "request_id": request["request_id"], "value": {}}


class HostApiTests(unittest.TestCase):
    """Host API contract exposed to the desktop shell."""
    def test_public_surface_contains_only_named_queries_and_safe_mutations(self) -> None:
        """The public surface exposes only approved queries and safe mutations."""
        public = {
            name for name, member in inspect.getmembers(HostApi, inspect.isfunction)
            if not name.startswith("_")
        }
        # The surface is pinned to exactly these queries and mutations
        self.assertEqual(
            public,
            {
                "get_application_snapshot",
                "get_lifecycle_schedule",
                "get_ui_preferences",
                "save_selected_profile",
                "save_backup_after_stop",
                "save_lifecycle_schedule",
                "read_operation_events",
                "get_operation",
                "request_operation_cancellation",
                "request_shutdown",
                "list_profiles",
                "list_mod_inventory",
                "read_profile",
                "preview_profile_command",
                "list_profile_missions",
                "provision_profile",
                "save_profile",
                "delete_profile",
                "list_backups",
                "list_backup_catalog",
                "select_backup_archive",
                "preview_profile_restore",
                "restore_profile_from_backup",
                "create_backup",
                "preview_restore",
                "apply_restore",
                "inspect_restore_recovery",
                "load_configuration",
                "preview_configuration",
                "apply_configuration",
                "load_mission_configuration",
                "preview_mission_configuration",
                "apply_mission_configuration",
                "convert_starter_loadout",
                "load_medical_features",
                "preview_medical_feature",
                "apply_medical_feature",
                "select_legacy_root",
                "preview_legacy_import",
                "apply_legacy_import",
                "list_legacy_backup_references",
                "revalidate_legacy_backup_references",
                "save_steam_settings",
                "authenticate_steamcmd",
                "update_workshop_items",
                "preview_mod_publication",
                "publish_mods_and_keys",
                "save_settings",
                "select_settings_path",
                "get_server_status",
                "start_server",
                "stop_server",
                "restart_server",
                "read_log",
            },
        )

    def test_named_methods_build_strict_bridge_requests(self) -> None:
        """Every named method dispatches its bridge method with strict parameters."""
        facade = RecordingFacade()
        api = HostApi(facade)  # type: ignore[arg-type]
        # Invoke every named method once against the recording facade
        api.get_application_snapshot()
        api.get_lifecycle_schedule("main")
        api.get_ui_preferences()
        api.save_selected_profile("main")
        api.save_backup_after_stop("main", True)
        api.save_lifecycle_schedule("main", 4, 30, "restart")
        api.read_operation_events(12, 25)
        api.get_operation("operation-1")
        api.request_operation_cancellation("operation-1")
        api.request_shutdown()
        api.list_profiles()
        api.list_mod_inventory("main")
        api.read_profile("main")
        api.preview_profile_command("main")
        api.list_profile_missions()
        api.provision_profile({"profile_id": "new"}, 4)
        api.save_profile({"profile_id": "main"}, 3)
        api.delete_profile("main", 3)
        api.list_backups("main")
        api.create_backup("main", 3, 4)
        api.preview_restore("main", "backup-1")
        api.apply_restore("main", "backup-1", 3, 4, "a" * 64, "b" * 64)
        api.inspect_restore_recovery()
        api.get_server_status()
        api.start_server("main", 3, 4)
        api.stop_server("main", 3, 4, True)
        api.restart_server("main", 3, 4, False)
        api.read_log("manager", 250)
        api.load_configuration("main", "server")
        api.preview_configuration("main", "server", 3, 4, "a" * 64, None, {"maxPlayers": 80})
        api.apply_configuration("main", "server", 3, 4, "a" * 64, None, {"maxPlayers": 80})
        api.load_mission_configuration("main", "economy")
        api.preview_mission_configuration("main", "economy", 3, 4, "a" * 64, {"ZombieMaxCount": 600})
        api.apply_mission_configuration("main", "economy", 3, 4, "a" * 64, {"ZombieMaxCount": 600})
        api.convert_starter_loadout("main", 3, 4, "a" * 64)
        api.load_medical_features("main")
        api.preview_medical_feature("main", "medical_loot_zones", True, 3, 4, "a" * 64)
        api.apply_medical_feature("main", "medical_loot_zones", True, 3, 4, "a" * 64)
        api._set_legacy_folder_selector(lambda: r"D:\Synthetic Legacy")
        api.select_legacy_root()
        api.preview_legacy_import("selection-1")
        api.apply_legacy_import("a" * 32, "b" * 64, ["profile:main.json"])
        api.list_legacy_backup_references()
        api.revalidate_legacy_backup_references(2)
        api.save_settings({
            "dayz_root": None, "steamcmd_root": None, "custom_backup_root": None,
        }, None)
        # The dispatch order matches the call order exactly
        self.assertEqual(
            [request["method"] for request in facade.requests],
            [
                "get_application_snapshot",
                "get_lifecycle_schedule",
                "get_ui_preferences",
                "save_selected_profile",
                "save_backup_after_stop",
                "save_lifecycle_schedule",
                "read_operation_events",
                "get_operation",
                "request_operation_cancellation",
                "request_shutdown",
                "list_profiles",
                "list_mod_inventory",
                "read_profile",
                "preview_profile_command",
                "list_profile_missions",
                "provision_profile",
                "save_profile",
                "delete_profile",
                "list_backups",
                "create_backup",
                "preview_restore",
                "apply_restore",
                "inspect_restore_recovery",
                "get_server_status",
                "start_server",
                "stop_server",
                "restart_server",
                "read_log",
                "load_configuration",
                "preview_configuration",
                "apply_configuration",
                "load_mission_configuration",
                "preview_mission_configuration",
                "apply_mission_configuration",
                "convert_starter_loadout",
                "load_medical_features",
                "preview_medical_feature",
                "apply_medical_feature",
                "select_legacy_root",
                "preview_legacy_import",
                "apply_legacy_import",
                "list_legacy_backup_references",
                "revalidate_legacy_backup_references",
                "save_settings",
            ],
        )
        self.assertTrue(all(request["contract_version"] == 1 for request in facade.requests))
        self.assertNotIn("review_state", str(facade.requests))
        # Index each request by method for the parameter assertions
        requests_by_method = {request["method"]: request for request in facade.requests}
        self.assertEqual(requests_by_method["stop_server"]["parameters"], {
            "profile_id": "main",
            "expected_profile_revision": 3,
            "expected_settings_revision": 4,
            "backup_after_stop": True,
        })
        self.assertEqual(
            requests_by_method["restart_server"]["parameters"]["backup_after_stop"],
            False,
        )

    def test_bridge_error_is_returned_in_sanitized_contract(self) -> None:
        """Bridge failures surface as sanitized error envelopes."""
        def expired(_parameters: object) -> object:
            """Fail the event read with an expired cursor error."""
            raise ApplicationCallError(ErrorCode.EVENT_CURSOR_EXPIRED, "Reload snapshot.")

        # Build a facade whose event read fails with that error
        api = HostApi(BridgeFacade({"read_operation_events": expired}))
        result = api.read_operation_events(0, 100)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "EVENT_CURSOR_EXPIRED")


if __name__ == "__main__":
    unittest.main()
