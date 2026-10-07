"""Ownership record and adoption (A6, design 9): one session launches, a later session adopts and stops."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.lifecycle import ServerLifecycleService  # noqa: E402
from dayz_serverman.application.lifecycle_coordinator import OTHER_PROFILE_RUNNING, for_running_profile  # noqa: E402
from dayz_serverman.application.lifecycle_ownership import LaunchOwnership  # noqa: E402
from dayz_serverman.application.server_readiness import ReadinessLifecycleService  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.domain.lifecycle import (  # noqa: E402
    InventorySnapshot, LifecycleFailure, ProcessObservation, ServerReadiness, ServerState,
)
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.repositories.json_store import VersionedJsonRepository  # noqa: E402
from dayz_serverman.repositories.lifecycle_state import LifecycleStateRepository  # noqa: E402
from dayz_serverman.repositories.server_ownership import OWNERSHIP_FILE, ServerOwnershipRepository  # noqa: E402
from tests.profile_fixtures import create_profile_paths, profile_payload  # noqa: E402
from tests.test_lifecycle_service import (  # noqa: E402
    FakeInventory, FakeLauncher, FakeMutex, FakeStopper, ProfilesStub, SettingsStub,
)
from tests.test_server_readiness import FakeMissionProbe, FakeProbe  # noqa: E402

PROFILE = "livonia-main"
# Wall clock of session A's launch and of session B's adoption, 90 s later
LAUNCH_NS = 1_000_000_000_000
ADOPT_NS = LAUNCH_NS + 90_000_000_000


class RecordingLogger:
    """Logger that keeps the event names of every record."""

    def __init__(self) -> None:
        """Start with no records."""
        self.events: list[str] = []

    def emit(self, event: str, **_fields: object) -> None:
        """Keep the event name."""
        self.events.append(event)


class Session:
    """One manager session over a shared fake process table: lifecycle, readiness and its ownership holder."""

    def __init__(self, test: OwnershipTests, name: str, *, root: Path | None = None, writable: bool = True,
                 wall_ns: int = LAUNCH_NS) -> None:
        """Build new service objects that share only the files and the process table with other sessions."""
        self.logger = RecordingLogger()
        self.ownership = LaunchOwnership(
            ServerOwnershipRepository(test.data / OWNERSHIP_FILE), root or test.manager,
            logger=self.logger, writable=writable, session_id=f"session-{name}",
        )
        self.launcher = FakeLauncher(test.inventory)
        self.stopper = FakeStopper(test.inventory)
        self.now = 5_000.0
        self.lifecycle = ServerLifecycleService(
            SettingsStub(test.settings), ProfilesStub(test.profile), test.inventory, self.launcher, self.stopper,
            FakeMutex(), LifecycleStateRepository(VersionedJsonRepository(test.data / "state.json"), name),
            test.data / "logs", self.ownership, wall_clock_ns=lambda: LAUNCH_NS,
        )
        self.probe, self.mission = FakeProbe(), FakeMissionProbe()
        self.readiness = ReadinessLifecycleService(
            self.lifecycle, ProfilesStub(test.profile), SettingsStub(test.settings), self.probe, self.mission,
            clock=lambda: self.now, wall_clock_ns=lambda: wall_ns, ownership=self.ownership,
        )


class OwnershipTests(unittest.TestCase):
    """Session A launches and writes the record; session B adopts, stops and clears it; mismatches adopt nothing."""

    def setUp(self) -> None:
        """Create a DayZ root with the profile, a manager data folder and a shared process table."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_ownership_")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.dayz, self.manager = base / "DayZ Root", base / "Manager"
        self.data = self.manager / "data"
        create_profile_paths(self.dayz)
        self.executable = self.dayz / "Bin" / "DayZ Server_x64.exe"
        self.settings = ManagerSettings(4, str(self.dayz.resolve()), str(self.executable.resolve()),
                                        None, None, None, None, MappingProxyType({}))
        self.profile = ProfileRecord(7, ProfileInput.parse(profile_payload()))
        self.inventory = FakeInventory()

    def launched(self) -> Session:
        """Start the server in session A and return it."""
        first = Session(self, "a")
        self.assertEqual(first.readiness.start(PROFILE, 7, 4).state, ServerState.RUNNING_MANAGED)
        return first

    def record(self) -> dict:
        """Return the stored ownership record."""
        return json.loads((self.data / OWNERSHIP_FILE).read_text(encoding="utf-8"))

    def test_session_b_adopts_shows_the_profile_stops_and_clears(self) -> None:
        """A launch writes the record; a new session adopts it, names the profile, stops with taskkill and clears it."""
        first = self.launched()
        stored = self.record()
        self.assertEqual(stored["schema_version"], 1)
        server = stored["server"]
        self.assertEqual((server["pid"], server["creation_time_ns"], server["profile_id"], server["session_id"]),
                         (700, 70_000, PROFILE, "session-a"))
        self.assertEqual(server["started_after_ns"], LAUNCH_NS)
        self.assertEqual(Path(stored["manager_root"]), Path(str(self.manager.resolve())))
        # data/state.json keeps its exact schema
        self.assertEqual(set(json.loads((self.data / "state.json").read_text(encoding="utf-8"))),
                         {"schema_version", "revision", "session_id", "launch_evidence"})
        self.assertIsNone(first.ownership.adopted_launch())
        second = Session(self, "b", wall_ns=ADOPT_NS)
        status = second.readiness.status()
        self.assertEqual((status.state, status.process_id, status.profile_id), (ServerState.RUNNING_MANAGED, 700, PROFILE))
        self.assertIsNotNone(second.ownership.adopted_launch())
        self.assertIsNone(second.ownership.current().handle_token)
        stopped = second.readiness.stop(4)
        self.assertEqual((stopped.state, second.stopper.calls), (ServerState.STOPPED, 1))
        self.assertIsNone(self.record()["server"])
        self.assertIsNone(second.ownership.current())
        self.assertEqual(second.logger.events, [])

    def test_the_record_query_port_comes_from_the_profile_configuration(self) -> None:
        """The staged hint of the launch carries the profile's query port into the record."""
        self.launched()
        self.assertIsInstance(self.record()["server"]["query_port"], int)

    def test_each_mismatch_adopts_nothing(self) -> None:
        """pid, path, creation time, manager root, two candidates, an incomplete inventory, a corrupt or newer record."""
        self.launched()
        path = str(self.executable.resolve())
        cases = {
            "pid": InventorySnapshot((ProcessObservation(701, path, 70_000),)),
            "creation time": InventorySnapshot((ProcessObservation(700, path, 70_001),)),
            "no creation time": InventorySnapshot((ProcessObservation(700, path, None),)),
            "two candidates": InventorySnapshot((ProcessObservation(700, path, 70_000),
                                                 ProcessObservation(702, path, 70_200))),
            "incomplete": InventorySnapshot((ProcessObservation(700, path, 70_000),), complete=False),
        }
        for name, snapshot in cases.items():
            with self.subTest(name):
                self.inventory.snapshot = snapshot
                session = Session(self, "b")
                self.assertNotEqual(session.readiness.status().state, ServerState.RUNNING_MANAGED)
                self.assertIsNone(session.ownership.current())
        self.inventory.snapshot = InventorySnapshot((ProcessObservation(700, path, 70_000),))
        other_root = Session(self, "b", root=self.manager.parent / "Other Manager")
        self.assertEqual(other_root.readiness.status().state, ServerState.RUNNING_EXTERNAL)
        stored = self.record()
        stored["server"]["executable_path"] = str(self.dayz / "Other" / "DayZServer_x64.exe")
        self.write_record(stored)
        self.assertEqual(Session(self, "b").readiness.status().state, ServerState.RUNNING_EXTERNAL)

    def write_record(self, document: dict) -> None:
        """Replace the stored record with the given document."""
        (self.data / OWNERSHIP_FILE).write_text(json.dumps(document), encoding="utf-8")

    def test_an_unreadable_record_adopts_nothing_and_is_logged_once(self) -> None:
        """A corrupt record, a newer schema or bad fields leave the server external; never 'stopped'."""
        self.launched()
        good = self.record()
        bad_fields = json.loads(json.dumps(good))
        bad_fields["server"]["pid"] = True
        extra = json.loads(json.dumps(good))
        extra["server"]["extra"] = 1
        for name, text in (("corrupt", "{broken"), ("future", json.dumps({**good, "schema_version": 2})),
                           ("bad fields", json.dumps(bad_fields)), ("extra field", json.dumps(extra))):
            with self.subTest(name):
                (self.data / OWNERSHIP_FILE).write_text(text, encoding="utf-8")
                session = Session(self, "b")
                for _ in range(2):
                    self.assertEqual(session.readiness.status().state, ServerState.RUNNING_EXTERNAL)
                self.assertEqual(session.logger.events, ["server_ownership.unreadable"])
                # The stop of an unowned server stays refused as today (criterion 15)
                with self.assertRaises(LifecycleFailure) as raised:
                    session.readiness.stop(4)
                self.assertEqual(raised.exception.code, "EXTERNAL_PROCESS")

    def test_a_leftover_staging_file_neither_hides_the_record_nor_blocks_its_next_write(self) -> None:
        """QF-33: an owner adopts from the whole record beside a crash leftover; its stop removes the leftover."""
        self.launched()
        leftover = self.data / f".{OWNERSHIP_FILE}.0123456789abcdef.tmp"
        leftover.write_text('{"partial": ', encoding="utf-8")
        second = Session(self, "b")
        self.assertEqual(second.readiness.status().state, ServerState.RUNNING_MANAGED)
        self.assertEqual(second.logger.events, [])
        self.assertEqual(second.readiness.stop(4).state, ServerState.STOPPED)
        self.assertFalse(leftover.exists())
        self.assertIsNone(self.record()["server"])
        self.assertEqual(second.logger.events, [])

    def test_an_unwritable_record_does_not_fail_the_start(self) -> None:
        """A corrupt record is not overwritten; the start succeeds and the refusal is logged."""
        self.data.mkdir(parents=True)
        (self.data / OWNERSHIP_FILE).write_text("{broken", encoding="utf-8")
        session = Session(self, "a")
        self.assertEqual(session.readiness.start(PROFILE, 7, 4).state, ServerState.RUNNING_MANAGED)
        self.assertEqual((self.data / OWNERSHIP_FILE).read_text(encoding="utf-8"), "{broken")
        # The start's own reconciliation read it first (no adoption), then the write refused it
        self.assertEqual(session.logger.events, ["server_ownership.unreadable", "server_ownership.unwritable"])

    def test_readiness_grace_counts_from_the_recorded_launch(self) -> None:
        """90 s after the launch, 30 s of grace are left; at 121 s the server is unresponsive."""
        self.launched()
        second = Session(self, "b", wall_ns=ADOPT_NS)
        self.assertEqual(second.readiness.status().readiness, ServerReadiness.STARTING)
        self.assertEqual(second.mission.calls[-1][1], LAUNCH_NS)
        second.now += 31
        self.assertEqual(second.readiness.status().readiness, ServerReadiness.UNRESPONSIVE)
        self.assertEqual(second.readiness.running_query_port(), self.record()["server"]["query_port"])

    def test_d11_refuses_another_profile_in_the_adopting_session(self) -> None:
        """for_running_profile sees the recorded profile in session B."""
        self.launched()
        second = Session(self, "b")
        calls: list[object] = []
        handler = for_running_profile(second.readiness, calls.append)
        with self.assertRaises(ApplicationCallError) as raised:
            handler({"profile_id": "chernarus-main"})
        self.assertEqual(raised.exception.safe_message, OTHER_PROFILE_RUNNING)
        handler({"profile_id": PROFILE})
        self.assertEqual(len(calls), 1)

    def test_close_guard_holds_only_for_an_own_running_launch(self) -> None:
        """OD3: shutdown_safe is false for this session's own running server and true for an adopted one."""
        first = self.launched()
        self.assertFalse(first.readiness.shutdown_safe())
        second = Session(self, "b")
        self.assertTrue(second.readiness.shutdown_safe())
        second.readiness.status()
        self.assertIsNotNone(second.ownership.adopted_launch())
        self.assertTrue(second.readiness.shutdown_safe())

    def test_an_observer_adopts_in_memory_and_never_writes(self) -> None:
        """writable false: status adopts; a start, stop or stage writes no record."""
        self.launched()
        before = (self.data / OWNERSHIP_FILE).read_bytes()
        observer = Session(self, "observer", writable=False)
        self.assertEqual(observer.readiness.status().profile_id, PROFILE)
        observer.ownership.clear()
        observer.ownership.stage_readiness(PROFILE, 1, None)
        self.assertEqual((self.data / OWNERSHIP_FILE).read_bytes(), before)

    def test_a_restart_in_the_adopting_session_writes_the_new_launch(self) -> None:
        """Session B restarts the adopted server; the record names the new process and session B."""
        self.launched()
        second = Session(self, "b")
        second.launcher.next_pid = 800
        self.assertEqual(second.readiness.restart(PROFILE, 7, 4).state, ServerState.RUNNING_MANAGED)
        server = self.record()["server"]
        self.assertEqual((server["pid"], server["session_id"]), (800, "session-b"))
        self.assertIsNone(second.ownership.adopted_launch())
        self.assertFalse(second.readiness.shutdown_safe())


if __name__ == "__main__":
    unittest.main()
