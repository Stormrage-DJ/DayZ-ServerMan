"""Schedule coordinator tests for validation, claiming, and persistence."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from dayz_serverman.application.log_activity import EVENT_TEXTS
from dayz_serverman.application.schedules import ScheduleCoordinator
from dayz_serverman.domain.lifecycle import LifecycleSnapshot, ServerState
from dayz_serverman.domain.schedules import DailySchedule, ScheduleValidationError
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.schedules import ScheduleRepository


class MutableClock:
    """Clock double whose value tests move forward manually."""
    def __init__(self, value: datetime) -> None:
        """Store the initial time."""
        self.value = value

    def __call__(self) -> datetime:
        """Return the current clock value."""
        return self.value


class FakeProfiles:
    """Profile port returning a fixed revision for the main profile."""
    def read(self, profile_id: object):
        """Return the main profile record and reject other identifiers."""
        if profile_id != "main":
            raise AssertionError("unexpected profile")
        return SimpleNamespace(revision=7, values=SimpleNamespace(profile_id="main"))


class FakeSettings:
    """Settings port returning a fixed revision."""
    def load(self):
        """Return settings with a fixed revision."""
        return SimpleNamespace(revision=9)


class FakePreferences:
    """Preference port that enables backup for the main profile."""
    def backup_after_stop(self, profile_id: str) -> bool:
        """Report the backup preference for the given profile."""
        return profile_id == "main"


class FakeLifecycle:
    """Lifecycle port reporting a fixed managed server state."""
    def __init__(self, state: ServerState = ServerState.RUNNING_MANAGED) -> None:
        """Store the state reported by status."""
        self.state = state
        # Profile that the server was started with; None when it is not known
        self.profile_id: str | None = None

    def status(self) -> LifecycleSnapshot:
        """Return a snapshot with a process id only while running managed."""
        running = self.state == ServerState.RUNNING_MANAGED
        return LifecycleSnapshot(self.state, 42 if running else None,
                                 profile_id=self.profile_id if running else None)


class FakeLifecycleCoordinator:
    """Lifecycle coordinator double that records stop and restart calls."""
    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, dict]] = []

    def stop_server(self, parameters: dict) -> dict[str, str]:
        """Record the stop request and return its operation id."""
        self.calls.append(("stop", parameters))
        return {"operation_id": "stop-operation"}

    def restart_server(self, parameters: dict) -> dict[str, str]:
        """Record the restart request and return its operation id."""
        self.calls.append(("restart", parameters))
        return {"operation_id": "restart-operation"}


class FakeLogger:
    """Logger double that keeps every emitted event with its fields."""
    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []

    def emit(self, event, *_args, **keywords) -> None:
        """Keep the event name and its fields."""
        self.events.append((event, dict(keywords.get("fields") or {})))


class ScheduleTests(unittest.TestCase):
    """Schedule coordinator contracts for validation, claiming, and persistence."""
    def setUp(self) -> None:
        """Create a schedule repository with a controllable clock."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_schedule_")
        path = Path(self.temporary.name) / "data" / "schedules.json"
        self.repository = ScheduleRepository(VersionedJsonRepository(path))
        self.clock = MutableClock(datetime(2026, 9, 29, 3, 0))
        self.lifecycle = FakeLifecycle()
        self.logger = FakeLogger()
        self.lifecycle_coordinator = FakeLifecycleCoordinator()
        self.coordinator = ScheduleCoordinator(
            self.repository,
            FakeProfiles(),
            FakeSettings(),
            FakePreferences(),
            self.lifecycle,
            self.lifecycle_coordinator,
            self.logger,
            clock=self.clock,
            poll_seconds=0.01,
        )

    def tearDown(self) -> None:
        """Stop the coordinator and remove the temporary store."""
        self.coordinator.stop()
        self.temporary.cleanup()

    def test_validates_time_and_boolean_is_not_an_integer(self) -> None:
        """Boolean and out-of-range time inputs are rejected."""
        with self.assertRaises(ScheduleValidationError):
            DailySchedule.requested("main", True, 0, "stop")
        with self.assertRaises(ScheduleValidationError):
            DailySchedule.requested("main", 23, 60, "restart")

    def test_persists_disabled_schedule_and_retains_time(self) -> None:
        """A disabled schedule persists without a next run time."""
        saved = self.coordinator.save_schedule({
            "profile_id": "main", "hour": 6, "minute": 45, "action": None,
        })
        self.assertFalse(saved["enabled"])
        self.assertIsNone(saved["next_run_local"])
        # Reloading keeps the saved hour and minute
        reloaded = self.coordinator.get_schedule({"profile_id": "main"})
        self.assertEqual((reloaded["hour"], reloaded["minute"]), (6, 45))

    def test_due_stop_is_claimed_once_and_uses_current_revisions(self) -> None:
        """A due stop is claimed once and uses current revisions."""
        self.coordinator.save_schedule({
            "profile_id": "main", "hour": 4, "minute": 0, "action": "stop",
        })
        # Move the clock to the scheduled minute
        self.clock.value = datetime(2026, 9, 29, 4, 0)
        self.assertEqual(self.coordinator.run_due(), 1)
        self.assertEqual(self.coordinator.run_due(), 0)
        self.assertEqual(len(self.lifecycle_coordinator.calls), 1)
        action, parameters = self.lifecycle_coordinator.calls[0]
        self.assertEqual(action, "stop")
        self.assertEqual(parameters, {
            "profile_id": "main",
            "expected_profile_revision": 7,
            "expected_settings_revision": 9,
            "backup_after_stop": True,
        })
        # The claimed run reports its status and next occurrence
        view = self.coordinator.get_schedule({"profile_id": "main"})
        self.assertEqual(view["last_status"], "QUEUED")
        self.assertEqual(view["next_run_local"], "2026-09-30T04:00")

    def test_due_restart_uses_restart_path(self) -> None:
        """A due restart uses the restart path."""
        self.coordinator.save_schedule({
            "profile_id": "main", "hour": 4, "minute": 5, "action": "restart",
        })
        self.clock.value = datetime(2026, 9, 29, 4, 5)
        self.coordinator.run_due()
        self.assertEqual(self.lifecycle_coordinator.calls[0][0], "restart")

    def test_due_action_is_skipped_when_server_is_not_managed(self) -> None:
        """A due action is skipped when the server is not managed."""
        self.lifecycle.state = ServerState.STOPPED
        self.coordinator.save_schedule({
            "profile_id": "main", "hour": 4, "minute": 0, "action": "stop",
        })
        self.clock.value = datetime(2026, 9, 29, 4, 0)
        self.coordinator.run_due()
        self.assertEqual(self.lifecycle_coordinator.calls, [])
        view = self.coordinator.get_schedule({"profile_id": "main"})
        self.assertEqual(view["last_status"], "SKIPPED_NOT_RUNNING")

    def test_due_action_is_skipped_while_another_profile_runs(self) -> None:
        """The schedule of one profile never stops the server of another profile."""
        self.lifecycle.profile_id = "other"
        for day, action in ((29, "stop"), (30, "restart")):
            self.clock.value = datetime(2026, 9, day, 3, 0)
            self.coordinator.save_schedule({
                "profile_id": "main", "hour": 4, "minute": 0, "action": action,
            })
            self.clock.value = datetime(2026, 9, day, 4, 0)
            self.assertEqual(self.coordinator.run_due(), 1)
        self.assertEqual(self.lifecycle_coordinator.calls, [])
        view = self.coordinator.get_schedule({"profile_id": "main"})
        self.assertEqual(view["last_status"], "SKIPPED_NOT_RUNNING")
        # The skip is recorded for the profile of the schedule, as for a stopped server
        skipped = [fields for event, fields in self.logger.events if event == "schedule.skipped"]
        self.assertEqual(skipped, [{"profile_id": "main", "server_state": "RUNNING_MANAGED"}] * 2)
        # The wording of a skipped run is true for a stopped server and for another profile's server
        self.assertEqual(
            EVENT_TEXTS["schedule.skipped"],
            "The scheduled action was skipped because its server was not running under the manager.",
        )

    def test_due_action_runs_for_the_running_profile_and_for_an_unknown_one(self) -> None:
        """The own profile is stopped; a server whose profile is not known keeps the earlier rule."""
        for day, running in ((29, "main"), (30, None)):
            self.lifecycle.profile_id = running
            self.lifecycle_coordinator.calls.clear()
            self.clock.value = datetime(2026, 9, day, 3, 0)
            self.coordinator.save_schedule({
                "profile_id": "main", "hour": 4, "minute": 0, "action": "stop",
            })
            self.clock.value = datetime(2026, 9, day, 4, 0)
            self.coordinator.run_due()
            self.assertEqual([call[0] for call in self.lifecycle_coordinator.calls], ["stop"], running)

    def test_start_after_today_time_does_not_catch_up(self) -> None:
        """Starting after today's time schedules the next day."""
        self.clock.value = datetime(2026, 9, 29, 5, 0)
        saved = self.coordinator.save_schedule({
            "profile_id": "main", "hour": 4, "minute": 0, "action": "stop",
        })
        self.assertEqual(saved["next_run_local"], "2026-09-30T04:00")
        self.assertEqual(self.coordinator.run_due(), 0)


if __name__ == "__main__":
    unittest.main()
