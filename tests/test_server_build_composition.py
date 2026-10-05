"""Wiring of the server build check: one shared guard, the drain listener and its own timer events."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from tests import server_build_fixtures  # noqa: F401
except ModuleNotFoundError:
    import server_build_fixtures  # noqa: F401

from dayz_serverman.application.update_check_scheduler import UpdateCheckScheduler
from dayz_serverman.composition import build_composition


class Recorder:
    """Logger stand-in."""

    def __init__(self) -> None:
        """Start without events."""
        self.events: list[str] = []

    def emit(self, event, level="INFO", fields=None):
        """Keep the event name."""
        self.events.append(event)


class IdleCheck:
    """A check that is never due."""

    def run_scheduled(self) -> bool:
        """Start nothing."""
        return False

    def seconds_until_due(self) -> float:
        """Wait the longest time."""
        return 3600.0

    def add_idle_listener(self, _listener) -> None:
        """Accept the listener."""


class CompositionTests(unittest.TestCase):
    """Detailed design 14.5 and 14.6: wiring only; no check runs."""

    def test_one_guard_for_every_steamcmd_user_and_close_cancels_the_check(self) -> None:
        """The Workshop update and the build check share the guard; closing cancels the check first."""
        with tempfile.TemporaryDirectory(prefix="serverman_build_") as temporary:
            composition = build_composition(Path(temporary) / "Manager")
            try:
                parts = composition.server_build
                self.assertIs(composition.workshop_updates._steamcmd_guard, parts.guard)
                self.assertIs(parts.service._guard, parts.guard)
                self.assertEqual(composition.paths.server_build_cache.name, "server-build-check.json")
                self.assertFalse(parts.service._shutdown.is_set())
                composition.shutdown.request_shutdown()
                self.assertTrue(parts.service._shutdown.is_set())
                self.assertFalse(parts.service.run_scheduled())
            finally:
                composition.operations.shutdown(2)

    def test_the_build_timer_names_its_own_events(self) -> None:
        """The second timer logs server_build events, the first keeps update_check events."""
        logger = Recorder()
        for prefix in ("server_build", "update_check"):
            scheduler = UpdateCheckScheduler(IdleCheck(), logger, event_prefix=prefix)
            scheduler.start()
            self.assertTrue(scheduler.stop(2.0))
        self.assertEqual(logger.events, ["server_build.scheduler_started", "server_build.scheduler_stopped",
                                         "update_check.scheduler_started", "update_check.scheduler_stopped"])


if __name__ == "__main__":
    unittest.main()
