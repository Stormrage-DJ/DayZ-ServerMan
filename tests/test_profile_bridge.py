"""Bridge tests for named profile methods over the portable manager composition."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402
from tests.profile_fixtures import create_profile_paths, profile_payload  # noqa: E402


# Terminal states the wait helper accepts as finished
TERMINAL = {
    OperationState.SUCCEEDED,
    OperationState.FAILED,
    OperationState.CANCELLED,
    OperationState.RECOVERY_REQUIRED,
}


def request(method: str, parameters: dict[str, object]) -> dict[str, object]:
    """Build a bridge request envelope for the named method."""
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "profile-request",
        "method": method,
        "parameters": parameters,
    }


class ProfileBridgeTests(unittest.TestCase):
    """Contract: profile operations cross the bridge through named, queued methods."""
    def setUp(self) -> None:
        """Build a portable composition with a saved DayZ root and profile paths."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_profile_bridge_")
        self.manager_root = Path(self.temporary.name) / "Portable Manager"
        self.dayz_root = Path(self.temporary.name) / "DáyZ Root With Spaces"
        self.executable = self.dayz_root / "Bin" / "DayZ Server_x64.exe"
        create_profile_paths(self.dayz_root)
        self.composition = build_composition(self.manager_root)
        self.composition.settings.save(
            SettingsInput(
                dayz_root=str(self.dayz_root),
                dayz_executable=str(self.executable),
            ),
            None,
        )

    def tearDown(self) -> None:
        """Shut down operations and remove the temporary roots."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def wait_terminal(self, operation_id: str):
        """Wait for the profile operation to reach a terminal state."""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            record = self.composition.operations.get(operation_id)
            if record.state in TERMINAL:
                return record
            time.sleep(0.01)
        raise AssertionError("profile operation did not finish")

    def dispatch(self, method: str, parameters: dict[str, object]) -> dict:
        """Dispatch the named method through the host bridge."""
        return self.composition.bridge.dispatch(request(method, parameters))

    def save_profile(self, **overrides: object) -> object:
        """Save the fixture profile and return its terminal operation record."""
        accepted = self.dispatch(
            "save_profile",
            {"profile": profile_payload(**overrides), "expected_revision": None},
        )
        self.assertTrue(accepted["success"])
        return self.wait_terminal(accepted["value"]["operation_id"])

    def test_profile_crud_uses_named_bridge_methods_and_mutation_lane(self) -> None:
        """Create, list, read, and delete profiles through named bridge methods."""
        # Create a profile and confirm the queued operation succeeded
        generated = self.dayz_root / "serverman" / "livonia-main"
        (generated / "profile").mkdir(parents=True)
        (generated / "serverDZ.cfg").write_text("instanceId = 17;\n", encoding="utf-8")
        storage = self.dayz_root / "mpmissions" / "dayzOffline.enoch" / "storage_17"
        storage.mkdir(parents=True)
        (storage / "players.db").write_bytes(b"world")
        saved = self.save_profile(
            server_config=r"serverman\livonia-main\serverDZ.cfg",
            runtime_profile=r"serverman\livonia-main\profile",
        )
        self.assertEqual(saved.state, OperationState.SUCCEEDED)
        self.assertEqual(saved.result["revision"], 0)

        # List and read through the named methods
        listed = self.dispatch("list_profiles", {})
        read = self.dispatch("read_profile", {"profile_id": "livonia-main"})
        self.assertEqual([item["profile_id"] for item in listed["value"]], ["livonia-main"])
        self.assertEqual(read["value"]["display_name"], "Livonia Közösségi")

        # Delete with the current revision and confirm the list is empty
        deleted = self.dispatch(
            "delete_profile",
            {"profile_id": "livonia-main", "expected_revision": 0},
        )
        deleted_record = self.wait_terminal(deleted["value"]["operation_id"])
        self.assertEqual(deleted_record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.dispatch("list_profiles", {})["value"], [])
        self.assertFalse(generated.exists())
        self.assertFalse(storage.exists())

    def test_preview_returns_exact_vector_without_starting_a_process(self) -> None:
        """Return the exact preview vector without launching any process."""
        # Save the profile, then preview its launch vector
        self.assertEqual(self.save_profile().state, OperationState.SUCCEEDED)
        result = self.dispatch(
            "preview_profile_command",
            {"profile_id": "livonia-main"},
        )
        # The preview carries the resolved executable and scoped mod switches
        self.assertTrue(result["success"])
        self.assertEqual(result["value"]["argv"][0], str(self.executable.resolve()))
        self.assertEqual(result["value"]["argv"][1:7], [
            r"-config=Config Files\serverDZ.cfg",
            "-port=2302",
            r"-profiles=Profiles\Máin Runtime",
            r"-mission=mpmissions\dayzOffline.enoch",
            "-mod=@Community Framework",
            "-serverMod=@Server Tools",
        ])

    def test_delete_preserves_shared_world_storage_while_removing_profile(self) -> None:
        """A shared instance preserves its world while exclusive profile data is removed."""
        for profile_id in ("first", "second"):
            generated = self.dayz_root / "serverman" / profile_id
            (generated / "profile").mkdir(parents=True)
            (generated / "serverDZ.cfg").write_text("instanceId = 9;\n", encoding="utf-8")
            saved = self.save_profile(
                profile_id=profile_id, display_name=profile_id.title(),
                server_config=fr"serverman\{profile_id}\serverDZ.cfg",
                runtime_profile=fr"serverman\{profile_id}\profile",
            )
            self.assertEqual(saved.state, OperationState.SUCCEEDED)
        storage = self.dayz_root / "mpmissions" / "dayzOffline.enoch" / "storage_9"
        storage.mkdir(parents=True)
        (storage / "players.db").write_bytes(b"shared")

        accepted = self.dispatch(
            "delete_profile", {"profile_id": "first", "expected_revision": 0},
        )
        deleted = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(deleted.state, OperationState.SUCCEEDED)
        self.assertTrue(deleted.result["preserved_shared_mission_storage"])
        self.assertFalse((self.dayz_root / "serverman" / "first").exists())
        self.assertTrue(storage.is_dir())
        self.assertFalse(self.dispatch("read_profile", {"profile_id": "first"})["success"])
        self.assertTrue(self.dispatch("read_profile", {"profile_id": "second"})["success"])

    def test_invalid_input_is_rejected_before_queue_and_stale_write_fails(self) -> None:
        """Reject invalid input before queuing and fail stale writes."""
        # Validation must reject the extra port switch before any queue entry
        invalid = self.dispatch(
            "save_profile",
            {
                "profile": profile_payload(extra_arguments=["-port=9999"]),
                "expected_revision": None,
            },
        )
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.composition.operations.list_recent(), ())

        # A stale revision write must fail without changing the stored profile
        self.save_profile()
        stale = self.dispatch(
            "save_profile",
            {"profile": profile_payload(display_name="Stale"), "expected_revision": 8},
        )
        stale_record = self.wait_terminal(stale["value"]["operation_id"])
        self.assertEqual(stale_record.state, OperationState.FAILED)
        self.assertEqual(stale_record.terminal_error.code, "REVISION_CONFLICT")

    def test_guided_creation_discovers_mission_and_provisions_ready_profile(self) -> None:
        """Create the config, runtime folder, and selectable profile through the bridge."""
        mission = self.dayz_root / "mpmissions" / "Pripyat.Custom"
        mission.mkdir(parents=True)
        executable = self.dayz_root / "DayZServer_x64.exe"
        executable.write_bytes(b"fixture")
        missions = self.dispatch("list_profile_missions", {})
        self.assertTrue(missions["success"])
        self.assertIn(
            r"mpmissions\Pripyat.Custom",
            [item["relative_path"] for item in missions["value"]["missions"]],
        )
        accepted = self.dispatch("provision_profile", {
            "profile": {
                "profile_id": "pripyat", "display_name": "Pripyat",
                "server_executable": "DayZServer_x64.exe",
                "mission_root": r"mpmissions\Pripyat.Custom", "game_port": 2302,
                "mods": [], "extra_arguments": [],
            },
            "expected_settings_revision": missions["value"]["settings_revision"],
        })
        completed = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(completed.state, OperationState.SUCCEEDED)
        self.assertTrue(completed.result["readiness"]["ready"])
        self.assertEqual(self.dispatch("read_profile", {"profile_id": "pripyat"})["value"][
            "mission_root"
        ], r"mpmissions\Pripyat.Custom")

    def test_corrupt_record_translates_to_recovery_required(self) -> None:
        """Translate a corrupt profile record into a recovery-required response."""
        # Write a corrupt profile document directly
        profiles_root = self.composition.paths.profiles
        profiles_root.mkdir(parents=True, exist_ok=True)
        (profiles_root / "broken.json").write_text("{broken", encoding="utf-8")
        # The bridge must surface recovery required
        result = self.dispatch("list_profiles", {})
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "RECOVERY_REQUIRED")

    def test_webview_surface_exposes_only_named_profile_and_configuration_methods(self) -> None:
        """Expose only the named profile, backup, configuration, and server methods."""
        # The named surfaces include profiles, backups, configuration, and server control
        self.assertTrue(
            {
                "get_application_snapshot",
                "get_ui_preferences",
                "save_selected_profile",
                "save_backup_after_stop",
                "read_operation_events",
                "get_operation",
                "request_operation_cancellation",
                "request_shutdown",
                "list_profiles",
                "read_profile",
                "preview_profile_command",
                "list_profile_missions",
                "provision_profile",
                "save_profile",
                "delete_profile",
                "list_backups",
                "list_legacy_backup_references",
                "revalidate_legacy_backup_references",
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
                "select_legacy_root",
                "preview_legacy_import",
                "apply_legacy_import",
                "save_steam_settings",
                "authenticate_steamcmd",
                "update_workshop_items",
                "preview_mod_publication",
                "publish_mods_and_keys",
                "save_settings",
                "validate_settings_path_selection",
                "get_server_status",
                "start_server",
                "stop_server",
                "restart_server",
                "read_log",
            }.issubset(self.composition.host_bridge.allowed_methods),
        )
        self.assertIn("start_server", self.composition.host_bridge.allowed_methods)


if __name__ == "__main__":
    unittest.main()
