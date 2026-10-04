"""Bridge tests for lifecycle status queries and operation lane mutations."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.lifecycle_coordinator import (  # noqa: E402
    OTHER_PROFILE_RUNNING,
    LifecycleCoordinator,
)
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.bridge.contracts import CONTRACT_VERSION  # noqa: E402
from dayz_serverman.bridge.facade import BridgeFacade  # noqa: E402
from dayz_serverman.domain.lifecycle import (  # noqa: E402
    LifecycleFailure,
    LifecycleSnapshot,
    ServerState,
)


class FakeLifecycle:
    """Scripted lifecycle port recording calls and injecting failures."""
    def __init__(self) -> None:
        """Initialize the call log and the optional failure to inject."""
        self.calls: list[tuple[object, ...]] = []
        self.failure: LifecycleFailure | None = None
        self.snapshot: LifecycleSnapshot | None = None

    def status(self) -> LifecycleSnapshot:
        """Report a stopped server, or the scripted snapshot of a running one."""
        return self.snapshot if self.snapshot is not None else LifecycleSnapshot(ServerState.STOPPED)

    def start(self, profile_id: str, profile_revision: int, settings_revision: int):
        """Record a start call and return the running result or inject a failure."""
        self.calls.append(("start", profile_id, profile_revision, settings_revision))
        return self._result(ServerState.RUNNING_MANAGED)

    def stop(self, settings_revision: int):
        """Record a stop call and return the stopped result or inject a failure."""
        self.calls.append(("stop", settings_revision))
        return self._result(ServerState.STOPPED)

    def restart(self, profile_id: str, profile_revision: int, settings_revision: int):
        """Record a restart call and return the running result or inject a failure."""
        self.calls.append(("restart", profile_id, profile_revision, settings_revision))
        return self._result(ServerState.RUNNING_MANAGED)

    def _result(self, state: ServerState) -> LifecycleSnapshot:
        """Return a snapshot for the state or raise the injected failure."""
        if self.failure is not None:
            raise self.failure
        return LifecycleSnapshot(state, 700 if state == ServerState.RUNNING_MANAGED else None)


class FakeBackups:
    """Scripted backup port sharing the lifecycle call log."""
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        """Store the shared call log and start without an injected failure."""
        self.calls = calls
        self.failure: Exception | None = None

    def create(self, profile_id, profile_revision, settings_revision, checkpoint):
        """Record a backup call, report progress, and raise any injected failure."""
        self.calls.append(("backup", profile_id, profile_revision, settings_revision))
        checkpoint("DISCOVER", 10)
        if self.failure is not None:
            raise self.failure
        return {"backup_id": "livonia-main_2026-09-28_19-00-00"}


def request(method: str, parameters: dict[str, object]) -> dict[str, object]:
    """Build a bridge request envelope with the shared lifecycle request id."""
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "lifecycle-request",
        "method": method,
        "parameters": parameters,
    }


class LifecycleBridgeTests(unittest.TestCase):
    """Status query and operation lane contracts of the lifecycle bridge."""
    def setUp(self) -> None:
        """Build the bridge with scripted lifecycle and backup ports."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_lifecycle_bridge_")
        self.lifecycle = FakeLifecycle()
        self.backups = FakeBackups(self.lifecycle.calls)
        self.operations = OperationManager(OperationStore(Path(self.temporary.name)))
        self.bridge = BridgeFacade(
            LifecycleCoordinator(self.lifecycle, self.operations, self.backups).handlers(),
        )

    def tearDown(self) -> None:
        """Shut the operation manager down and remove the temporary store."""
        self.operations.shutdown(2)
        self.temporary.cleanup()

    def wait_terminal(self, operation_id: str):
        """Poll until the lifecycle operation reaches a terminal state."""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            record = self.operations.get(operation_id)
            if record.state in {
                OperationState.SUCCEEDED,
                OperationState.FAILED,
                OperationState.RECOVERY_REQUIRED,
            }:
                return record
            time.sleep(0.01)
        raise AssertionError("lifecycle operation did not finish")

    def dispatch(self, method: str, parameters: dict[str, object]) -> dict:
        """Dispatch a bridge request through the facade."""
        return self.bridge.dispatch(request(method, parameters))

    def test_status_is_a_responsive_query_and_mutations_use_operation_lane(self) -> None:
        """Status answers inline while mutations run through the operation lane."""
        # The status query answers immediately without an operation
        status = self.dispatch("get_server_status", {})
        self.assertEqual(status["value"]["state"], "STOPPED")
        # The additive fields are present and empty for a stopped server
        self.assertEqual(status["value"], {
            "state": "STOPPED", "process_id": None, "diagnostic_code": None, "readiness": None,
            "query_port": None, "profile_id": None, "started_at": None,
        })
        self.assertEqual(CONTRACT_VERSION, 1)
        # A start request queues and completes on the operation lane
        accepted = self.dispatch(
            "start_server",
            {
                "profile_id": "livonia-main",
                "expected_profile_revision": 7,
                "expected_settings_revision": 4,
            },
        )
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.lifecycle.calls, [("start", "livonia-main", 7, 4)])

    def test_unsupported_stop_is_a_stable_durable_operation_failure(self) -> None:
        """An unproven stop method becomes a stable durable operation failure."""
        self.lifecycle.failure = LifecycleFailure(
            "STOP_METHOD_UNPROVEN",
            "Graceful stop is unavailable.",
        )
        accepted = self.dispatch("stop_server", self.control_parameters(False))
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.FAILED)
        self.assertEqual(record.terminal_error.code, "STOP_METHOD_UNPROVEN")
        self.assertNotIn("Traceback", record.terminal_error.message)

    def test_recovery_failure_uses_recovery_required_terminal_state(self) -> None:
        """A recovery-required failure maps to the recovery terminal state."""
        self.lifecycle.failure = LifecycleFailure(
            "RECOVERY_REQUIRED",
            "Lifecycle state requires recovery.",
            recovery_required=True,
        )
        accepted = self.dispatch("stop_server", self.control_parameters(False))
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.RECOVERY_REQUIRED)

    def test_unknown_fields_and_invalid_revisions_are_rejected_before_queue(self) -> None:
        """Unknown fields and invalid revision types are rejected before queueing."""
        invalid = self.dispatch(
            "start_server",
            {
                "profile_id": "livonia-main",
                "expected_profile_revision": True,
                "expected_settings_revision": 4,
                "shell": "cmd.exe",
            },
        )
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.operations.list_recent(), ())

    def test_stop_with_backup_runs_after_verified_stop(self) -> None:
        """The backup runs only after the stop is verified."""
        accepted = self.dispatch("stop_server", self.control_parameters(True))
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.lifecycle.calls, [
            ("stop", 4), ("backup", "livonia-main", 7, 4),
        ])
        self.assertIn("backup_id", record.result["backup"])

    def test_restart_with_backup_orders_stop_backup_start(self) -> None:
        """Restart with backup runs stop, backup, and start in that order."""
        accepted = self.dispatch("restart_server", self.control_parameters(True))
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.lifecycle.calls, [
            ("stop", 4), ("backup", "livonia-main", 7, 4),
            ("start", "livonia-main", 7, 4),
        ])

    def test_backup_failure_leaves_restart_stopped(self) -> None:
        """A backup failure during restart leaves the server stopped."""
        self.backups.failure = OSError("synthetic backup failure")
        accepted = self.dispatch("restart_server", self.control_parameters(True))
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.FAILED)
        self.assertEqual(self.lifecycle.calls, [
            ("stop", 4), ("backup", "livonia-main", 7, 4),
        ])
        self.assertEqual(record.terminal_error.code, "STORAGE_FAILURE")

    def test_status_names_the_running_profile_and_its_start_time(self) -> None:
        """A managed server reports the profile it was started with and when."""
        self.lifecycle.snapshot = LifecycleSnapshot(
            ServerState.RUNNING_MANAGED, 700, profile_id="livonia-main",
            started_at="2026-10-04T07:49:52.000+00:00",
        )
        status = self.dispatch("get_server_status", {})["value"]
        self.assertEqual(status["profile_id"], "livonia-main")
        self.assertEqual(status["started_at"], "2026-10-04T07:49:52.000+00:00")

    def test_stop_and_restart_for_another_profile_are_refused_before_the_queue(self) -> None:
        """D11: a request for a profile that is not the running one stops nothing."""
        self.lifecycle.snapshot = LifecycleSnapshot(
            ServerState.RUNNING_MANAGED, 700, profile_id="livonia-main",
            started_at="2026-10-04T07:49:52.000+00:00",
        )
        for method in ("stop_server", "restart_server"):
            for backup in (False, True):
                with self.subTest(method=method, backup=backup):
                    refused = self.dispatch(method, self.control_parameters(backup, "other"))
                    self.assertFalse(refused["success"])
                    self.assertEqual(refused["error"]["code"], "INVALID_REQUEST")
                    self.assertEqual(refused["error"]["message"], OTHER_PROFILE_RUNNING)
        self.assertEqual(self.lifecycle.calls, [])
        self.assertEqual(self.operations.list_recent(), ())
        # The same request for the running profile is accepted
        accepted = self.dispatch("stop_server", self.control_parameters(True))
        record = self.wait_terminal(accepted["value"]["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.lifecycle.calls, [("stop", 4), ("backup", "livonia-main", 7, 4)])

    def test_unknown_running_profile_leaves_the_state_rules_in_charge(self) -> None:
        """Without a known running profile the request is queued as before."""
        self.lifecycle.snapshot = LifecycleSnapshot(ServerState.RUNNING_MANAGED, 700)
        accepted = self.dispatch("stop_server", self.control_parameters(False, "other"))
        self.assertTrue(accepted["success"])
        self.wait_terminal(accepted["value"]["operation_id"])
        # A scheduled action calls the plain method and is not changed by the check
        self.lifecycle.snapshot = LifecycleSnapshot(
            ServerState.RUNNING_MANAGED, 700, profile_id="livonia-main")
        coordinator = LifecycleCoordinator(self.lifecycle, self.operations, self.backups)
        scheduled = coordinator.stop_server(self.control_parameters(False, "other"))
        self.wait_terminal(scheduled["operation_id"])
        # The refusal text is plain wording: no identifier that the catalogue would hide
        self.assertNotRegex(OTHER_PROFILE_RUNNING, r"[A-Z]{2,}|_|[a-z][A-Z]")

    @staticmethod
    def control_parameters(backup: bool, profile_id: str = "livonia-main") -> dict[str, object]:
        """Build control parameters for a profile at revision seven."""
        return {
            "profile_id": profile_id,
            "expected_profile_revision": 7,
            "expected_settings_revision": 4,
            "backup_after_stop": backup,
        }


if __name__ == "__main__":
    unittest.main()
