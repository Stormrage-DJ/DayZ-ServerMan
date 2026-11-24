"""Crash stop paths (plan 3.2): every deliberate stop records its intent, so no crash restart follows."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import MappingProxyType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.crash_restart import CrashRestartMonitor  # noqa: E402
from dayz_serverman.application.lifecycle_ownership import LaunchOwnership  # noqa: E402
from dayz_serverman.application.mod_restart_coordinator import ModRestartCoordinator  # noqa: E402
from dayz_serverman.application.schedule_dispatch import ScheduleDispatcher  # noqa: E402
from dayz_serverman.application.server_readiness import ReadinessLifecycleService  # noqa: E402
from dayz_serverman.application.shutdown import ShutdownCoordinator  # noqa: E402
from dayz_serverman.domain.lifecycle import (  # noqa: E402
    InventorySnapshot, LaunchEvidence, LifecycleFailure, ServerReadiness, ServerState,
)
from dayz_serverman.domain.models import ManagerSettings  # noqa: E402
from dayz_serverman.domain.profiles import ProfileInput, ProfileRecord  # noqa: E402
from dayz_serverman.domain.schedules import DailySchedule, ScheduleAction  # noqa: E402
from dayz_serverman.observability.structured_log import NullStructuredLogger  # noqa: E402
from tests.crash_scenario_support import ScenarioCase  # noqa: E402
from tests.profile_fixtures import profile_payload  # noqa: E402
from tests.test_lifecycle_service import ProfilesStub, SettingsStub  # noqa: E402
from tests.test_server_ownership import PROFILE, Session  # noqa: E402
from tests.test_server_readiness import (  # noqa: E402
    FakeLifecycle, FakeMissionProbe, FakeProbe, _Profiles, _Settings,
)

ARGS = {"profile_id": PROFILE, "expected_profile_revision": 7, "expected_settings_revision": 4,
        "backup_after_stop": False}


class ScriptedBackups:
    """Backup service stub that records a call; no stop path may reach it."""

    def __init__(self) -> None:
        """Start without calls."""
        self.calls: list[tuple[object, ...]] = []

    def create(self, *args: object) -> dict:
        """Record the call."""
        self.calls.append(args)
        return {}


class ScriptedPreferences:
    """Preference stub: no backup after a stop."""

    def backup_after_stop(self, profile_id: str) -> bool:
        """Return False for every profile."""
        del profile_id
        return False


class ScriptedPublication:
    """Publication stub that confirms the plan and reports a verified start."""

    def confirm_restart_plan(self, request, fingerprint) -> bool:
        """Confirm every plan."""
        del request, fingerprint
        return True

    def publish(self, request, fingerprint, progress, **handover) -> dict:
        """Return a verified, started result."""
        del request, fingerprint, progress, handover
        return {"profile_id": PROFILE, "publication_state": "VERIFIED", "start_state": "STARTED"}


class StopPathTests(ScenarioCase):
    """Each path starts from a fresh session whose process ends during a failed stop request."""

    def path_cases(self, chain, session) -> dict:
        """Return the six stop paths as functions without arguments."""
        dispatcher = ScheduleDispatcher(ProfilesStub(self.profile), SettingsStub(self.settings),
                                        ScriptedPreferences(), session.readiness, chain.coordinator)
        mod_restart = ModRestartCoordinator(ScriptedPublication(), session.readiness, None, chain.operations)
        mod_request = {"profile_id": PROFILE, "expected_profile_revision": 7,
                       "expected_semantic_profile_digest": "a" * 64, "expected_settings_revision": 4,
                       "update_operation_id": "update-1", "publication_fingerprint": "b" * 64,
                       "backup_after_stop": False}
        return {
            "operator stop": lambda: chain.coordinator.stop_server(dict(ARGS)),
            "operator restart": lambda: chain.coordinator.restart_server(dict(ARGS)),
            "backup chain after stop": lambda: chain.coordinator.stop_server({**ARGS, "backup_after_stop": True}),
            "scheduled stop": lambda: dispatcher.execute(DailySchedule(PROFILE, action=ScheduleAction.STOP)),
            "scheduled restart": lambda: dispatcher.execute(DailySchedule(PROFILE, action=ScheduleAction.RESTART)),
            "mod restart": lambda: mod_restart.apply(mod_request),
        }

    def test_every_stop_path_records_the_intent_and_no_restart_follows(self) -> None:
        """Criterion 5: the process ends during a failed stop request, and still nothing restarts it."""
        names = ("operator stop", "operator restart", "backup chain after stop", "scheduled stop",
                 "scheduled restart", "mod restart")
        for number, name in enumerate(names):
            with self.subTest(path=name):
                self.inventory.snapshot = InventorySnapshot(())
                session = Session(self, f"path{number}")
                session.readiness.start(PROFILE, 7, 4)
                backups = ScriptedBackups()
                chain = self.chain(session, backups)
                self.ready(session)
                chain.at(0)
                token = session.ownership.current().launch_token
                session.stopper.on_stop = lambda session=session: self.crash(session)
                session.stopper.failure = LifecycleFailure(
                    "STOP_FAILED", "the stop request failed after the process ended")
                self.path_cases(chain, session)[name]()
                chain.settle()
                for seconds in (5, 10, 15, 20):
                    chain.at(seconds)
                chain.settle()
                self.assertEqual(len(session.launcher.requests), 1)
                self.assertIsNone(chain.flow.read())
                self.assertTrue(session.ownership.launch_facts(token).stop_intent)
                self.assertEqual(session.stopper.calls, 1)
                if name == "backup chain after stop":
                    self.assertEqual(backups.calls, [])

    def test_the_same_end_without_a_stop_path_is_a_crash(self) -> None:
        """Control of the table: the same end, with no stop path, arms a restart."""
        session = self.launched()
        chain = self.chain(session)
        self.ready(session)
        chain.at(0)
        self.crash(session)
        chain.at(5)
        self.assertEqual(chain.flow.read().kind, "pending")


class PidGuardTests(unittest.TestCase):
    """The READY latch belongs to the launch that the status describes."""

    def setUp(self) -> None:
        """Build the readiness service over a scripted lifecycle, as ServerReadinessTests does, with ownership."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_pid_guard_")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config = root / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text('hostname = "test";\nsteamQueryPort = 2405;\n', encoding="utf-8")
        profile = ProfileRecord(7, ProfileInput.parse(profile_payload()))
        settings = ManagerSettings(4, str(root), str(root / "DayZServer_x64.exe"),
                                   None, None, None, None, MappingProxyType({}))
        self.ownership = LaunchOwnership()
        self.probe, self.mission = FakeProbe(), FakeMissionProbe()
        self.service = ReadinessLifecycleService(
            FakeLifecycle(), _Profiles(profile), _Settings(settings), self.probe, self.mission,
            clock=lambda: 1_000.0, wall_clock_ns=lambda: 50_000, ownership=self.ownership,
        )

    def test_a_ready_status_of_another_process_does_not_latch_the_launch(self) -> None:
        """Vigil recorded this gap open after task 1.1: a restart between the status and the evidence read."""
        # The row of the other process runs first, so the token has no latch when the row of the same process runs
        expected = {701: False, 700: True}
        for pid, latched in expected.items():
            with self.subTest(evidence_pid=pid):
                self.service.start("livonia-main", 7, 4)
                self.ownership.set(LaunchEvidence(pid, "c:\\dayz\\dayzserver_x64.exe", pid * 100, "tok-x", None))
                self.probe.ready = True
                self.mission.ready = True
                self.assertEqual(self.service.status().readiness, ServerReadiness.READY)
                self.assertEqual(self.ownership.launch_facts("tok-x").ready, latched)


class CloseTests(ScenarioCase):
    """Closing the manager ends the monitor; it never stops a running server and never starts one."""

    def test_closing_the_manager_leaves_a_running_server_alone(self) -> None:
        """Criterion 5: the drain stops the monitor; the server keeps running."""
        session = self.launched()
        chain = self.chain(session)
        self.ready(session)
        shutdown = ShutdownCoordinator(chain.operations, NullStructuredLogger(), session.readiness.shutdown_safe)
        monitor = CrashRestartMonitor(chain.flow, poll_seconds=3600.0)
        self.addCleanup(monitor.stop)
        shutdown.add_drain_listener(monitor.stop)
        monitor.start()
        shutdown.request_shutdown()
        self.assertFalse(monitor.is_running())
        self.assertEqual([item.name for item in threading.enumerate() if item.name == "dayz-serverman-crash-restart"], [])
        self.assertEqual(session.stopper.calls, 0)
        self.assertEqual(session.readiness.status().state, ServerState.RUNNING_MANAGED)

    def test_a_restart_that_waits_when_the_window_closes_never_starts(self) -> None:
        """Criteria 5 and 6: the lane refuses the start after the drain, so the restart gives up."""
        session = self.launched()
        chain = self.chain(session)
        self.ready(session)
        chain.at(0)
        self.crash(session)
        chain.at(5)
        ShutdownCoordinator(chain.operations, NullStructuredLogger(), session.readiness.shutdown_safe).request_shutdown()
        chain.at(15)
        self.assertEqual(len(session.launcher.requests), 1)
        state = chain.flow.read()
        self.assertEqual((state.kind, state.reason), ("gave_up", "start_failed"))


if __name__ == "__main__":
    unittest.main()
