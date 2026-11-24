"""Crash restart monitor (plan 2.1): the daemon thread, its stop and the single start of two monitors."""

from __future__ import annotations

import dataclasses
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tests.crash_restart_support import Rig
from tests.test_ui_host_runtime import FRONTEND, FakeWebview, RecordingScheduler

from dayz_serverman.application.crash_restart import CrashRestartMonitor  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.composition_model import SessionMode  # noqa: E402
from dayz_serverman.host.runtime import launch_application  # noqa: E402
from dayz_serverman.application.crash_restart_flow import CrashRestartFlow  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.shutdown import ShutdownCoordinator  # noqa: E402
from dayz_serverman.observability.structured_log import NullStructuredLogger  # noqa: E402

THREAD_NAME = "dayz-serverman-crash-restart"


def alive_threads() -> list[threading.Thread]:
    """Return the live monitor threads."""
    return [item for item in threading.enumerate() if item.name == THREAD_NAME and item.is_alive()]


class MonitorTests(unittest.TestCase):
    """The monitor polls in one daemon thread and stops on request."""

    def test_two_monitors_over_one_crash_start_once(self) -> None:
        """Two sessions that see the same end claim it once: one restart."""
        rig = Rig()
        second = CrashRestartFlow(
            rig.lifecycle, rig.ownership, rig.lane, rig.logger,
            clock=lambda: rig.clock[0], wall_clock=lambda: datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC),
        )
        monitors = [CrashRestartMonitor(rig.flow), CrashRestartMonitor(second)]
        rig.running()
        rig.clock[0] = 0.0
        for monitor in monitors:
            monitor.tick()
        rig.ended()
        for seconds in (5.0, 10.0, 15.0):
            rig.clock[0] = seconds
            for monitor in monitors:
                monitor.tick()
        self.assertEqual(rig.lane.submits, [("livonia-main",)])

    def test_start_and_stop_end_the_thread(self) -> None:
        """A started monitor runs; stop ends the thread."""
        monitor = CrashRestartMonitor(Rig().flow)
        monitor.start()
        self.assertTrue(monitor.is_running())
        self.assertTrue(monitor.stop(2.0))
        self.assertFalse(monitor.is_running())

    def test_a_second_start_keeps_one_thread(self) -> None:
        """Two start calls give one thread."""
        monitor = CrashRestartMonitor(Rig().flow)
        self.addCleanup(monitor.stop)
        monitor.start()
        monitor.start()
        self.assertEqual(len(alive_threads()), 1)
        self.assertTrue(monitor.stop())

    def test_the_drain_listener_stops_the_monitor(self) -> None:
        """The shutdown drain ends the monitor before the lane stops."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_crash_monitor_")
        self.addCleanup(temporary.cleanup)
        operations = OperationManager(OperationStore(Path(temporary.name)))
        self.addCleanup(operations.shutdown, 2)
        shutdown = ShutdownCoordinator(operations, NullStructuredLogger())
        monitor = CrashRestartMonitor(Rig().flow)
        self.addCleanup(monitor.stop)
        shutdown.add_drain_listener(monitor.stop)
        monitor.start()
        self.assertTrue(monitor.is_running())
        shutdown.request_shutdown()
        self.assertFalse(monitor.is_running())


class RecordingMonitor:
    """Monitor stand-in that records start and stop in a shared event list."""

    def __init__(self, events: list[str]) -> None:
        """Share the event log with the fake webview."""
        self.events = events

    def start(self) -> None:
        """Record the start."""
        self.events.append("crash-start")

    def stop(self, timeout: float = 2.0) -> bool:
        """Record the stop."""
        self.events.append("crash-stop")
        return True


class WiringTests(unittest.TestCase):
    """The composition holds the monitor; the owner window starts it and every window stops it."""

    def setUp(self) -> None:
        """Build a composition in a temporary manager directory; the two timers are only recorded."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_crash_wiring_")
        self.addCleanup(self.temporary.cleanup)
        self.manager_root = Path(self.temporary.name) / "Review Manager"
        self.composed = build_composition(self.manager_root)
        self.addCleanup(self.composed.operations.shutdown, 2)
        self.composition = dataclasses.replace(
            self.composed, update_check_scheduler=RecordingScheduler([]),
            server_build=dataclasses.replace(self.composed.server_build, scheduler=RecordingScheduler([])),
        )

    def launch(self, composition) -> list[str]:
        """Run the window with a fake webview and return the shared event list."""
        events: list[str] = []
        fake = FakeWebview()
        fake.events = events
        composition = dataclasses.replace(composition, crash_restart=RecordingMonitor(events))
        with patch("dayz_serverman.host.runtime.find_webview2_version", return_value="1.0"):
            launch_application(composition, frontend_root=FRONTEND, webview_module=fake)
        return events

    def test_the_composition_holds_a_stopped_monitor(self) -> None:
        """An owner and an observer composition each hold a monitor that does not run."""
        observer = build_composition(self.manager_root, mode=SessionMode.OBSERVER)
        for composition in (self.composed, observer):
            self.assertIsInstance(composition.crash_restart, CrashRestartMonitor)
            self.assertFalse(composition.crash_restart.is_running())

    def test_the_window_starts_and_stops_the_monitor_for_an_owner(self) -> None:
        """An owner window starts the monitor; an observer window never does; both stop it."""
        self.assertEqual(self.launch(self.composition), ["crash-start", "window", "crash-stop"])
        observer = dataclasses.replace(self.composition, mode=SessionMode.OBSERVER)
        self.assertEqual(self.launch(observer), ["window", "crash-stop"])

    def test_closing_the_application_stops_the_monitor(self) -> None:
        """The shutdown drain stops the monitor of a real composition."""
        monitor = self.composed.crash_restart
        self.addCleanup(monitor.stop)
        monitor.start()
        self.assertTrue(monitor.is_running())
        self.composed.shutdown.request_shutdown()
        self.assertFalse(monitor.is_running())


if __name__ == "__main__":
    unittest.main()
