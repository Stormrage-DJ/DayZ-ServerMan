"""Crash restart clear (plan 2.2, rework QF-001): an operator start or restart drops a stored crash state."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.crash_restart_bridge import CrashRestartHandlers  # noqa: E402
from tests.crash_restart_support import Rig  # noqa: E402
from tests.crash_scenario_support import ScenarioCase  # noqa: E402
from tests.test_server_ownership import PROFILE  # noqa: E402


class ScriptedSource:
    """Handler source with the status, start and restart handlers."""

    def handlers(self) -> dict:
        """Return the ordered table."""
        return {
            "get_server_status": lambda parameters: {"state": "STOPPED"},
            "start_server": lambda parameters: {},
            "restart_server": lambda parameters: {},
        }


class ClearTests(unittest.TestCase):
    """The flow drops a pending restart and any stored state."""

    def test_clear_drops_a_pending_restart_and_the_state(self) -> None:
        """A pending restart is dropped with its state; no restart follows."""
        rig = Rig()
        rig.running()
        rig.at(0)
        rig.ended()
        rig.at(5)
        self.assertTrue(rig.flow.clear())
        self.assertIsNone(rig.flow.read())
        rig.at(10)
        rig.at(15)
        self.assertEqual(rig.lane.submits, [])

    def test_clear_with_nothing_stored_returns_false(self) -> None:
        """Nothing stored gives False."""
        self.assertFalse(Rig().flow.clear())


class HandlerTests(unittest.TestCase):
    """An operator start or restart clears a finished notice."""

    def build(self) -> tuple:
        """Return a rig and the wrapped handler table over it."""
        rig = Rig()
        return rig, CrashRestartHandlers(ScriptedSource(), rig.flow).handlers()

    @staticmethod
    def field(table) -> object:
        """Read the auto_restart field of the wrapped status handler."""
        return table["get_server_status"]({})["auto_restart"]

    def test_an_operator_start_clears_a_give_up_state(self) -> None:
        """A start after a give-up shows no give-up note."""
        rig, table = self.build()
        rig.flow._restart_times = [100.0, 200.0, 300.0]
        rig.running()
        rig.at(380)
        rig.ended()
        rig.at(400)
        self.assertEqual(self.field(table)["kind"], "gave_up")
        table["start_server"]({})
        self.assertIsNone(self.field(table))

    def test_an_operator_restart_of_a_running_server_clears_a_restarted_state(self) -> None:
        """A restart of the restarted server shows no restarted note."""
        rig, table = self.build()
        rig.running()
        rig.at(0)
        rig.ended()
        for seconds in (5, 10, 15):
            rig.at(seconds)
        rig.running(token="tok-b", handle="h-b", ready=False)
        rig.at(20)
        self.assertEqual(self.field(table)["kind"], "restarted")
        table["restart_server"]({})
        self.assertIsNone(self.field(table))


class ClearScenarioTests(ScenarioCase):
    """The same, over the real lifecycle, ownership and lane."""

    def cycle(self, session, chain, start: float) -> None:
        """One crash and its restart: READY, a crash at start + 5 s, and the restart at start + 15 s."""
        self.ready(session)
        chain.at(start)
        self.crash(session)
        for seconds in (5, 10, 15):
            chain.at(start + seconds)
        chain.settle()

    def test_after_a_give_up_an_operator_start_and_stop_leave_no_note(self) -> None:
        """QF-001: the give-up note does not return after an operator start and a stop."""
        session = self.launched()
        chain = self.chain(session)
        for start in (0, 40, 80):
            self.cycle(session, chain, start)
        self.ready(session)
        chain.at(120)
        self.crash(session)
        chain.at(125)
        handlers = CrashRestartHandlers(chain.coordinator, chain.flow).handlers()
        self.assertEqual(handlers["get_server_status"]({})["auto_restart"]["kind"], "gave_up")
        handlers["start_server"]({"profile_id": PROFILE, "expected_profile_revision": 7,
                                  "expected_settings_revision": 4})
        chain.settle()
        session.readiness.stop(4)
        chain.at(130)
        status = handlers["get_server_status"]({})
        self.assertEqual(status["state"], "STOPPED")
        self.assertIsNone(status["auto_restart"])


if __name__ == "__main__":
    unittest.main()
