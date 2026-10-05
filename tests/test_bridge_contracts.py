"""Bridge facade tests for allowlists, sanitization, and operation events."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.bridge.contracts import CONTRACT_VERSION, ErrorCode  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.application.operations.models import OperationState  # noqa: E402


def request(method: str, parameters: dict | None = None, **overrides: object) -> dict:
    """Build a request envelope with optional field overrides."""
    value = {
        "contract_version": CONTRACT_VERSION,
        "request_id": "request-1",
        "method": method,
        "parameters": parameters or {},
    }
    value.update(overrides)
    return value


def wait_terminal(composition, operation_id: str, timeout: float = 2.0):
    """Wait for a terminal operation state and return its record."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = composition.operations.get(operation_id)
        if record.state in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
            OperationState.RECOVERY_REQUIRED,
        }:
            return record
        time.sleep(0.01)
    raise AssertionError("operation did not reach a terminal state")


class BridgeContractTests(unittest.TestCase):
    """Bridge facade contracts for allowlists, errors, and event reads."""
    def setUp(self) -> None:
        """Build an isolated composition and cache its bridge."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_bridge_")
        self.composition = build_composition(Path(self.temporary.name) / "Manager")
        self.bridge = self.composition.bridge

    def tearDown(self) -> None:
        """Shut the operations down and remove the temporary manager."""
        self.composition.operations.shutdown(2)
        self.temporary.cleanup()

    def assert_error(self, result: dict, code: ErrorCode) -> None:
        """Assert the result failed with the expected error code."""
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], code.value)

    def test_allowlist_contains_only_named_foundation_methods(self) -> None:
        """The allowlist exposes only the named foundation methods."""
        self.assertEqual(
            self.bridge.allowed_methods,
            {
                "delete_profile",
                "get_application_snapshot",
                "get_lifecycle_schedule",
                "get_ui_preferences",
                "get_operation",
                "get_server_status",
                "get_online_players",
                "list_profiles",
                "list_mod_inventory",
                "get_update_status",
                "request_update_check",
                "verify_mod_files",
                "list_backups",
                "list_backup_catalog",
                "inspect_backup_archive",
                "preview_profile_restore",
                "restore_profile_from_backup",
                "list_legacy_backup_references",
                "revalidate_legacy_backup_references",
                "create_backup",
                "preview_restore",
                "apply_restore",
                "inspect_restore_recovery",
                "preview_profile_command",
                "list_profile_missions",
                "provision_profile",
                "read_profile",
                "read_operation_events",
                "request_operation_cancellation",
                "request_shutdown",
                "save_profile",
                "save_selected_profile",
                "save_backup_after_stop",
                "save_automatic_update_checks",
                "save_lifecycle_schedule",
                "save_settings",
                "validate_settings_path_selection",
                "start_server",
                "stop_server",
                "restart_server",
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
                "save_steam_settings",
                "authenticate_steamcmd",
                "update_workshop_items",
                "preview_mod_publication",
                "publish_mods_and_keys",
                "apply_mods_and_restart",
                "read_log",
            },
        )

    def test_unknown_method_is_rejected(self) -> None:
        """Unknown methods are rejected as invalid requests."""
        result = self.bridge.dispatch(request("read_arbitrary_file"))
        self.assert_error(result, ErrorCode.INVALID_REQUEST)

    def test_unknown_request_field_is_rejected(self) -> None:
        """Unknown request fields are rejected."""
        raw = request("get_application_snapshot")
        raw["filesystem_path"] = "C:/secret"
        self.assert_error(self.bridge.dispatch(raw), ErrorCode.INVALID_REQUEST)

    def test_unsupported_contract_version_is_rejected(self) -> None:
        """Unsupported contract versions are rejected."""
        result = self.bridge.dispatch(
            request("get_application_snapshot", contract_version=999)
        )
        self.assert_error(result, ErrorCode.CONTRACT_VERSION_UNSUPPORTED)

    def test_invalid_request_id_is_not_echoed(self) -> None:
        """An unsafe request id is replaced instead of echoed."""
        result = self.bridge.dispatch(
            request("get_application_snapshot", request_id="<script>bad</script>")
        )
        self.assertEqual(result["request_id"], "invalid-request")

    def test_unknown_method_parameter_is_rejected(self) -> None:
        """Unknown method parameters are rejected."""
        result = self.bridge.dispatch(
            request("get_application_snapshot", {"shell": "cmd.exe"})
        )
        self.assert_error(result, ErrorCode.INVALID_REQUEST)

    def test_raw_handler_exception_is_sanitized(self) -> None:
        """Raw handler exceptions are sanitized to internal failures."""
        facade = BridgeFacade(
            {"safe_query": lambda _parameters: (_ for _ in ()).throw(RuntimeError("secret"))}
        )
        result = facade.dispatch(request("safe_query"))
        self.assert_error(result, ErrorCode.INTERNAL_FAILURE)
        self.assertNotIn("secret", result["error"]["message"])

    def test_snapshot_query_remains_available(self) -> None:
        """The snapshot query stays available with settings and a session id."""
        result = self.bridge.dispatch(request("get_application_snapshot"))
        self.assertTrue(result["success"])
        self.assertIn("settings", result["value"])
        self.assertIn("operation_session_id", result["value"])

    def test_snapshot_query_remains_responsive_during_mutation(self) -> None:
        """Snapshot queries stay responsive while a mutation lane is blocked."""
        # Block the operation lane with a controllable fixture
        started = threading.Event()
        release = threading.Event()
        operation = self.composition.operations.submit(
            "BLOCKING_FIXTURE",
            lambda _context: (started.set(), release.wait(2), {})[-1],
        )
        started.wait(1)
        # The query must return quickly even while the lane is blocked
        before = time.monotonic()
        result = self.bridge.dispatch(request("get_application_snapshot"))
        elapsed = time.monotonic() - before
        release.set()
        wait_terminal(self.composition, operation.operation_id)
        self.assertTrue(result["success"])
        self.assertLess(elapsed, 0.5)

    def test_save_settings_returns_operation_and_stale_revision_is_translated(self) -> None:
        """Save settings returns an operation and translates stale revisions."""
        first = self.bridge.dispatch(
            request("save_settings", {"expected_revision": None})
        )
        self.assertTrue(first["success"])
        first_record = wait_terminal(self.composition, first["value"]["operation_id"])
        self.assertEqual(first_record.state, OperationState.SUCCEEDED)

        # A stale revision must surface as a translated conflict error
        stale = self.bridge.dispatch(
            request("save_settings", {"expected_revision": 99})
        )
        stale_record = wait_terminal(self.composition, stale["value"]["operation_id"])
        self.assertEqual(stale_record.state, OperationState.FAILED)
        self.assertEqual(stale_record.terminal_error.code, ErrorCode.REVISION_CONFLICT.value)
        self.assertNotIn("Traceback", stale_record.terminal_error.message)

    def test_operation_events_are_returned_as_versioned_values(self) -> None:
        """Operation events are returned as versioned values."""
        accepted = self.bridge.dispatch(
            request("save_settings", {"expected_revision": None})
        )
        wait_terminal(self.composition, accepted["value"]["operation_id"])
        result = self.bridge.dispatch(
            request("read_operation_events", {"after_sequence": 0, "maximum": 100})
        )
        self.assertTrue(result["success"])
        self.assertGreaterEqual(len(result["value"]["events"]), 3)
        self.assertTrue(
            all(event["schema_version"] == 1 for event in result["value"]["events"])
        )


if __name__ == "__main__":
    unittest.main()
