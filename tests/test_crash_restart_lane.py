"""Crash restart plumbing (plan 2.1): the crash start in the operation lane and the release of a dead handle."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.crash_restart_lane import CrashRestartLane  # noqa: E402
from dayz_serverman.application.lifecycle import ServerLifecycleService  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import LifecycleCoordinator  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationRecord, OperationState  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.server_readiness import ReadinessLifecycleService  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerState  # noqa: E402

TERMINAL_STATES = {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.RECOVERY_REQUIRED}


class RecordingLogger:
    """Logger that keeps the event name and the other arguments of every record."""

    def __init__(self) -> None:
        """Start with no records."""
        self.records: list[tuple[str, dict]] = []

    def emit(self, event: str, **kwargs: object) -> None:
        """Keep the event and its arguments."""
        self.records.append((event, kwargs))


class ScriptedLifecycle:
    """Lifecycle port that records start calls."""

    def __init__(self) -> None:
        """Start without calls."""
        self.calls: list[tuple[object, ...]] = []

    def start(self, profile_id: str, profile_revision: int, settings_revision: int, *, before_change=None):
        """Record the call and return a running snapshot."""
        self.calls.append(("start", profile_id, profile_revision, settings_revision))
        return LifecycleSnapshot(ServerState.RUNNING_MANAGED, 700)


class StartAfterCrashTests(unittest.TestCase):
    """The crash start runs through the operation lane and checks that it is still wanted."""

    def setUp(self) -> None:
        """Build the lane with a scripted lifecycle and a recording logger."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_crash_lane_")
        self.addCleanup(self.temporary.cleanup)
        self.logger = RecordingLogger()
        self.lifecycle = ScriptedLifecycle()
        self.operations = OperationManager(OperationStore(Path(self.temporary.name)), logger=self.logger)
        self.addCleanup(self.operations.shutdown, 2)
        self.coordinator = LifecycleCoordinator(self.lifecycle, self.operations)

    def wait_terminal(self, operation_id: str):
        """Poll until the operation reaches a terminal state."""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            record = self.operations.get(operation_id)
            if record.state in TERMINAL_STATES:
                return record
            time.sleep(0.01)
        raise AssertionError("operation did not finish")

    def test_a_wanted_start_runs_the_lifecycle_start(self) -> None:
        """A start that is still wanted calls the lifecycle start once, as START_SERVER."""
        queued = self.coordinator.start_after_crash("livonia-main", 7, 4, lambda: True)
        record = self.wait_terminal(queued["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(record.kind, "START_SERVER")
        self.assertEqual(self.lifecycle.calls, [("start", "livonia-main", 7, 4)])

    def test_an_unwanted_start_does_nothing(self) -> None:
        """A start that is no longer wanted ends well without a lifecycle call."""
        queued = self.coordinator.start_after_crash("livonia-main", 7, 4, lambda: False)
        record = self.wait_terminal(queued["operation_id"])
        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(self.lifecycle.calls, [])
        self.assertEqual(dict(record.result), {"skipped": "not_wanted"})

    def test_the_operation_carries_the_crash_trigger(self) -> None:
        """The log record of the operation names the trigger and the kind."""
        queued = self.coordinator.start_after_crash("livonia-main", 7, 4, lambda: True)
        self.wait_terminal(queued["operation_id"])
        fields = [kwargs.get("fields") or {} for _event, kwargs in self.logger.records]
        self.assertTrue(any(
            item.get("trigger") == "crash_restart" and item.get("kind") == "START_SERVER" for item in fields
        ))


class ScriptedLauncher:
    """Launcher port that records the released handle tokens."""

    def __init__(self) -> None:
        """Start without released tokens."""
        self.released: list[str] = []

    def release_handle(self, token: str) -> None:
        """Record the token."""
        self.released.append(token)


class ReleaseHandleTests(unittest.TestCase):
    """The release of a dead launch's handle reaches the launcher; none is released for an adopted launch."""

    def setUp(self) -> None:
        """Build the lifecycle service over a scripted launcher."""
        self.launcher = ScriptedLauncher()
        self.service = ServerLifecycleService(None, None, None, self.launcher, None, None, None, Path("."))

    def test_release_handle_reaches_the_launcher(self) -> None:
        """A token is passed on."""
        self.service.release_handle("h1")
        self.assertEqual(self.launcher.released, ["h1"])

    def test_release_handle_without_token_does_nothing(self) -> None:
        """An adopted launch has no token; nothing is released."""
        self.service.release_handle(None)
        self.assertEqual(self.launcher.released, [])

    def test_the_readiness_service_delegates(self) -> None:
        """The readiness decorator passes the token to the lifecycle service."""
        ReadinessLifecycleService(self.service, None, None, None, None).release_handle("h2")
        self.assertEqual(self.launcher.released, ["h2"])


class Stub:
    """Profile and settings stub: read and load return the stub, which carries a revision."""

    def __init__(self, revision: int | None) -> None:
        """Keep the revision."""
        self.revision = revision

    def read(self, profile_id: str) -> "Stub":
        """Return the stub for any profile."""
        del profile_id
        return self

    def load(self) -> "Stub":
        """Return the stub."""
        return self


class ScriptedOperations:
    """Operation manager that returns the records it was given."""

    def __init__(self, *records: OperationRecord) -> None:
        """Keep the records."""
        self.records = records

    def list_recent(self) -> tuple[OperationRecord, ...]:
        """Return all records."""
        return self.records

    def get(self, operation_id: str) -> OperationRecord:
        """Return the record with this id."""
        return next(item for item in self.records if item.operation_id == operation_id)


class ScriptedCoordinator:
    """Coordinator that stores the crash start arguments."""

    def __init__(self) -> None:
        """Start without a call."""
        self.calls: list[tuple[object, ...]] = []

    def start_after_crash(self, *args: object) -> dict[str, str]:
        """Store the arguments and return a fixed operation."""
        self.calls.append(args)
        return {"operation_id": "op-7", "state": "QUEUED"}


def record(operation_id: str, kind: str, state: OperationState = OperationState.SUCCEEDED) -> OperationRecord:
    """Build an operation record."""
    return OperationRecord(operation_id, kind, state, "2026-11-23T12:00:00.000+00:00")


class LaneTests(unittest.TestCase):
    """The lane adapter lists lifecycle work, submits with current revisions and reads a state."""

    def test_lifecycle_operation_ids_keep_only_lifecycle_kinds(self) -> None:
        """Settings and backup operations are not lifecycle work."""
        operations = ScriptedOperations(
            record("a", "START_SERVER"), record("b", "STOP_SERVER"), record("c", "RESTART_SERVER"),
            record("d", "APPLY_MODS_AND_RESTART"), record("e", "SAVE_SETTINGS"), record("f", "CREATE_BACKUP"),
        )
        lane = CrashRestartLane(Stub(7), Stub(4), ScriptedCoordinator(), operations)
        self.assertEqual(lane.lifecycle_operation_ids(), frozenset({"a", "b", "c", "d"}))

    def test_submit_uses_the_current_revisions_and_returns_the_operation_id(self) -> None:
        """The profile and settings revisions come from the services at submit time."""
        coordinator = ScriptedCoordinator()
        lane = CrashRestartLane(Stub(7), Stub(4), coordinator, ScriptedOperations())

        def check() -> bool:
            return True

        self.assertEqual(lane.submit("livonia-main", check), "op-7")
        self.assertEqual(coordinator.calls, [("livonia-main", 7, 4, check)])

    def test_a_missing_settings_revision_is_an_error(self) -> None:
        """Without a settings revision nothing is submitted."""
        coordinator = ScriptedCoordinator()
        lane = CrashRestartLane(Stub(7), Stub(None), coordinator, ScriptedOperations())
        with self.assertRaises(RuntimeError):
            lane.submit("livonia-main", lambda: True)
        self.assertEqual(coordinator.calls, [])

    def test_state_reads_the_operation_record(self) -> None:
        """The state of the record is returned as it is."""
        lane = CrashRestartLane(Stub(7), Stub(4), ScriptedCoordinator(),
                                ScriptedOperations(record("g", "START_SERVER", OperationState.FAILED)))
        self.assertEqual(lane.state("g"), OperationState.FAILED)


if __name__ == "__main__":
    unittest.main()
