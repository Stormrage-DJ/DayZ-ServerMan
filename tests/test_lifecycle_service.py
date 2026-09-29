"""Service-level lifecycle tests for launch ordering, gates, and state evidence."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.lifecycle import ServerLifecycleService  # noqa: E402
from dayz_serverman.application.lifecycle_ports import LaunchReceipt, LaunchRequest  # noqa: E402
from dayz_serverman.domain.lifecycle import (  # noqa: E402
    InventorySnapshot,
    LifecycleFailure,
    ProcessObservation,
    ServerState,
)
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.repositories.json_store import VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.lifecycle_state import LifecycleStateRepository  # noqa: E402
from tests.profile_fixtures import create_profile_paths, profile_payload  # noqa: E402


@dataclass
class FakeInventory:
    """Scripted inventory port returning a fixed snapshot or raising an error."""
    snapshot: InventorySnapshot = InventorySnapshot(())
    error: Exception | None = None

    def candidates(self, expected_executable: str) -> InventorySnapshot:
        """Return the fixed snapshot or raise the scripted error."""
        del expected_executable
        if self.error is not None:
            raise self.error
        return self.snapshot


@dataclass
class FakeLauncher:
    """Scripted launcher port that records requests and fakes launch handles."""
    inventory: FakeInventory
    requests: list[LaunchRequest] = field(default_factory=list)
    fail: bool = False
    next_pid: int = 700
    on_launch: Callable[[], None] | None = None
    _handles: dict[str, int] = field(default_factory=dict)

    def launch(self, request: LaunchRequest) -> LaunchReceipt:
        """Record the request, publish a synthetic process, and return the receipt."""
        self.requests.append(request)
        if self.fail:
            raise OSError("synthetic launch failure")
        if self.on_launch is not None:
            self.on_launch()
        pid = self.next_pid
        self.next_pid += 1
        handle = f"handle-{pid}"
        self._handles[handle] = pid
        executable = request.argv[0]
        self.inventory.snapshot = InventorySnapshot(
            (ProcessObservation(pid, executable, pid * 100),)
        )
        return LaunchReceipt(pid, pid * 100, handle)

    def retains_handle(self, handle_token: str, pid: int) -> bool:
        """Report whether the handle token still refers to the process id."""
        return self._handles.get(handle_token) == pid

    def release_handle(self, handle_token: str) -> None:
        """Forget the handle token."""
        self._handles.pop(handle_token, None)


@dataclass
class FakeStopper:
    """Scripted stopper port that empties the inventory on success."""
    inventory: FakeInventory
    failure: LifecycleFailure | None = None
    calls: int = 0
    on_stop: Callable[[], None] | None = None

    def request_stop(self, evidence) -> None:
        """Count the stop request, then empty the inventory or raise the failure."""
        del evidence
        self.calls += 1
        if self.on_stop is not None:
            self.on_stop()
        if self.failure is not None:
            raise self.failure
        self.inventory.snapshot = InventorySnapshot(())


@dataclass
class FakeMutex:
    """Scripted control mutex that can simulate a competing manager."""
    conflict: bool = False
    entries: int = 0

    @contextmanager
    def guard(self, dayz_root: str):
        """Count guarded entries or raise a synthetic control conflict."""
        del dayz_root
        if self.conflict:
            raise LifecycleFailure("CONTROL_CONFLICT", "synthetic competing manager")
        self.entries += 1
        yield


class SettingsStub:
    """Settings port stub returning one fixed manager settings record."""
    def __init__(self, value: ManagerSettings) -> None:
        """Store the settings record returned by every load."""
        self.value = value

    def load(self) -> ManagerSettings:
        """Return the stored manager settings."""
        return self.value


class ProfilesStub:
    """Profile port stub that serves only the fixture profile identifier."""
    def __init__(self, value: ProfileRecord) -> None:
        """Store the fixture profile record."""
        self.value = value

    def read(self, profile_id: str) -> ProfileRecord:
        """Return the fixture profile or fail for any other identifier."""
        if profile_id != self.value.values.profile_id:
            raise LookupError("missing fixture")
        return self.value


class LifecycleServiceTests(unittest.TestCase):
    """Launch, stop, and restart contracts of the server lifecycle service."""
    def setUp(self) -> None:
        """Assemble the service with scripted ports and a temporary state store."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_lifecycle_")
        root = Path(self.temporary.name)
        self.dayz_root = root / "DáyZ Root With Spaces"
        self.executable = self.dayz_root / "Bin" / "DayZ Server_x64.exe"
        create_profile_paths(self.dayz_root)
        self.settings = ManagerSettings(
            4,
            str(self.dayz_root.resolve()),
            str(self.executable.resolve()),
            None,
            None,
            None,
            None,
            MappingProxyType({}),
        )
        self.profile = ProfileRecord(7, ProfileInput.parse(profile_payload()))
        self.inventory = FakeInventory()
        self.launcher = FakeLauncher(self.inventory)
        self.stopper = FakeStopper(self.inventory)
        self.mutex = FakeMutex()
        self.state_path = root / "manager" / "data" / "state.json"
        state = LifecycleStateRepository(VersionedJsonRepository(self.state_path), "session-a")
        self.service = ServerLifecycleService(
            SettingsStub(self.settings),
            ProfilesStub(self.profile),
            self.inventory,
            self.launcher,
            self.stopper,
            self.mutex,
            state,
            root / "manager" / "data" / "logs",
        )

    def tearDown(self) -> None:
        """Remove the temporary lifecycle layout."""
        self.temporary.cleanup()

    def start(self):
        """Start the standard profile at revision seven against revision four."""
        return self.service.start("livonia-main", 7, 4)

    def test_start_uses_exact_vector_and_records_evidence_before_success(self) -> None:
        """Start uses the exact launch vector and records evidence before success."""
        # Capture the service state at the moment of launch
        observed_phases = []
        self.launcher.on_launch = lambda: observed_phases.append(self.service.status().state)
        result = self.start()
        self.assertEqual(result.state, ServerState.RUNNING_MANAGED)
        # Verify the exact working directory and argument vector
        request = self.launcher.requests[0]
        self.assertEqual(request.working_directory, self.dayz_root.resolve())
        self.assertEqual(request.argv[0], str(self.executable.resolve()))
        self.assertEqual(request.argv[1:7], (
            r"-config=Config Files\serverDZ.cfg",
            "-port=2302",
            r"-profiles=Profiles\Máin Runtime",
            r"-mission=mpmissions\dayzOffline.enoch",
            "-mod=@Community Framework",
            "-serverMod=@Server Tools",
        ))
        # The persisted state must carry the recorded launch evidence
        persisted = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["session_id"], "session-a")
        self.assertEqual(persisted["launch_evidence"]["pid"], result.process_id)
        self.assertEqual(observed_phases, [ServerState.STARTING])

    def test_revision_mutex_recovery_and_process_gates_prevent_launch(self) -> None:
        """Stale revisions, control conflicts, and live processes all block launch."""
        # Stale profile and settings revisions must be rejected
        cases = (
            ("revision", lambda: self.service.start("livonia-main", 6, 4), "REVISION_CONFLICT"),
            ("settings", lambda: self.service.start("livonia-main", 7, 3), "REVISION_CONFLICT"),
        )
        for name, action, code in cases:
            with self.subTest(name=name), self.assertRaises(LifecycleFailure) as raised:
                action()
            self.assertEqual(raised.exception.code, code)
        # A competing manager must hold the control mutex
        self.mutex.conflict = True
        with self.assertRaises(LifecycleFailure) as raised:
            self.start()
        self.assertEqual(raised.exception.code, "CONTROL_CONFLICT")
        self.mutex.conflict = False
        # An already running external process must block launch too
        self.inventory.snapshot = InventorySnapshot(
            (ProcessObservation(99, str(self.executable.resolve()), 1),)
        )
        with self.assertRaises(LifecycleFailure) as raised:
            self.start()
        self.assertEqual(raised.exception.code, "EXTERNAL_PROCESS")
        self.assertEqual(self.launcher.requests, [])

    def test_corrupt_state_and_incomplete_inventory_fail_closed(self) -> None:
        """Corrupt persisted state and incomplete inventory both fail closed."""
        # Corrupt state must require recovery
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(LifecycleFailure) as raised:
            self.start()
        self.assertTrue(raised.exception.recovery_required)
        self.state_path.unlink()
        # An incomplete inventory must also fail closed
        self.inventory.snapshot = InventorySnapshot((), complete=False)
        with self.assertRaises(LifecycleFailure) as raised:
            self.start()
        self.assertEqual(raised.exception.code, "PROCESS_STATE_UNKNOWN")
        self.assertEqual(self.launcher.requests, [])

    def test_valid_json_with_unknown_lifecycle_shape_requires_recovery(self) -> None:
        """Valid JSON with an unknown lifecycle shape requires recovery."""
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            json.dumps({"schema_version": 1, "revision": 0, "unexpected": True}),
            encoding="utf-8",
        )
        with self.assertRaises(LifecycleFailure) as raised:
            self.start()
        self.assertTrue(raised.exception.recovery_required)
        self.assertEqual(self.launcher.requests, [])

    def test_launch_failure_is_safe_and_does_not_create_evidence(self) -> None:
        """A launch failure is safe and leaves no persisted evidence behind."""
        self.launcher.fail = True
        with self.assertRaises(LifecycleFailure) as raised:
            self.start()
        self.assertEqual(raised.exception.code, "LAUNCH_FAILED")
        self.assertFalse(self.state_path.exists())
        self.assertEqual(self.service.status().state, ServerState.STOPPED)

    def test_restart_never_launches_after_graceful_stop_rejection(self) -> None:
        """A rejected graceful stop must prevent the restart from launching."""
        self.start()
        self.service._stopper = FakeStopper(
            self.inventory,
            LifecycleFailure("STOP_METHOD_UNPROVEN", "Graceful stop is unavailable."),
        )
        with self.assertRaises(LifecycleFailure) as raised:
            self.service.restart("livonia-main", 7, 4)
        self.assertEqual(raised.exception.code, "STOP_METHOD_UNPROVEN")
        self.assertEqual(len(self.launcher.requests), 1)
        self.assertEqual(self.service.status().state, ServerState.RUNNING_MANAGED)

    def test_fake_graceful_stop_and_restart_obey_stop_then_start_order(self) -> None:
        """Stop and restart obey the stop then start order with visible phases."""
        self.start()
        # Capture the service state while the stop is in progress
        observed_phases = []
        self.stopper.on_stop = lambda: observed_phases.append(self.service.status().state)
        stopped = self.service.stop(4)
        self.assertEqual(stopped.state, ServerState.STOPPED)
        self.assertEqual(self.stopper.calls, 1)
        self.assertEqual(observed_phases, [ServerState.STOPPING])
        self.start()
        restarted = self.service.restart("livonia-main", 7, 4)
        self.assertEqual(restarted.state, ServerState.RUNNING_MANAGED)
        self.assertEqual(self.stopper.calls, 2)
        self.assertEqual(len(self.launcher.requests), 3)

    def test_restart_never_launches_after_stop_failure(self) -> None:
        """A stop failure must prevent the restart from launching again."""
        self.start()
        self.stopper.failure = LifecycleFailure("STOP_FAILED", "synthetic stop failure")
        with self.assertRaises(LifecycleFailure):
            self.service.restart("livonia-main", 7, 4)
        self.assertEqual(len(self.launcher.requests), 1)

    def test_restart_checks_profile_revision_before_requesting_stop(self) -> None:
        """A stale profile revision must fail before any stop request is sent."""
        self.start()
        with self.assertRaises(LifecycleFailure) as raised:
            self.service.restart("livonia-main", 6, 4)
        self.assertEqual(raised.exception.code, "REVISION_CONFLICT")
        self.assertEqual(self.stopper.calls, 0)
        self.assertEqual(len(self.launcher.requests), 1)


if __name__ == "__main__":
    unittest.main()
