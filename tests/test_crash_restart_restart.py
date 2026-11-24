"""Crash restart flow (plan 2.1): the wait, the single restart, the check in the lane and the follow-up."""

from __future__ import annotations

import unittest

from tests.crash_restart_support import WALL_TEXT, Rig

from dayz_serverman.application.crash_restart_flow import CrashRestartState  # noqa: E402
from dayz_serverman.application.operations.models import OperationState  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402


class RestartTests(unittest.TestCase):
    """Timeline typed by hand: running poll at 0 s, end seen at 5 s, wait poll at 10 s, restart poll at 15 s."""

    def setUp(self) -> None:
        """Start with a rig whose launch ran and ended."""
        self.rig = Rig()
        self.rig.running()
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)

    def submitted(self) -> Rig:
        """Run the polls at 10 and 15 s and return the rig with one submitted restart."""
        self.rig.at(10)
        self.rig.at(15)
        return self.rig

    def test_wait_then_one_restart(self) -> None:
        """No submit before 10 s after the end; one submit at 15 s; no second one at 20 s."""
        self.assertEqual(self.rig.lane.submits, [])
        self.rig.at(10)
        self.assertEqual(self.rig.lane.submits, [])
        self.rig.at(15)
        self.assertEqual(self.rig.lane.submits, [("livonia-main",)])
        self.rig.at(20)
        self.assertEqual(self.rig.lane.submits, [("livonia-main",)])

    def test_an_operator_operation_after_the_crash_blocks_the_restart(self) -> None:
        """An operation that the operator added after the crash cancels the restart."""
        self.rig.lane.ids.add("op-9")
        self.rig.at(10)
        self.rig.at(15)
        self.assertEqual(self.rig.lane.submits, [])
        self.assertIsNone(self.rig.flow.read())

    def test_cancel_during_the_wait(self) -> None:
        """A cancel at 10 s clears the state; nothing follows."""
        self.rig.at(10)
        self.assertTrue(self.rig.flow.cancel())
        self.assertIsNone(self.rig.flow.read())
        self.rig.at(15)
        self.rig.at(20)
        self.assertEqual(self.rig.lane.submits, [])
        self.assertEqual(self.rig.logger.events(), ["server_crash.restart_pending"])

    def test_the_check_inside_the_lane_refuses_when_not_wanted(self) -> None:
        """The lane check is True while wanted; a running server, an operator operation or a cancel make it False."""
        rig = self.submitted()
        check = rig.lane.last_check
        self.assertTrue(check())
        rig.lifecycle.state = ServerState.RUNNING_MANAGED
        self.assertFalse(check())
        rig.lifecycle.state = ServerState.STOPPED
        self.assertTrue(check())
        rig.lane.ids.add("op-9")
        self.assertFalse(check())
        rig.lane.states["op-1"] = OperationState.SUCCEEDED
        rig.at(20)
        self.assertIsNone(rig.flow.read())
        # A cancel after the submit makes the queued start do nothing; the polls again run on a fresh rig
        other = Rig()
        other.running()
        other.at(0)
        other.ended()
        for seconds in (5, 10, 15):
            other.at(seconds)
        self.assertTrue(other.lane.last_check())
        self.assertTrue(other.flow.cancel())
        self.assertFalse(other.lane.last_check())

    def test_a_failed_submit_gives_up(self) -> None:
        """A refused submit shows gave_up with start_failed."""
        self.rig.lane.fail_submit = RuntimeError("refused")
        self.rig.at(10)
        self.rig.at(15)
        self.assertEqual(self.rig.flow.read(), CrashRestartState("gave_up", "start_failed", WALL_TEXT))
        self.assertEqual(self.rig.logger.events().count("server_crash.gave_up"), 1)

    def test_a_failed_start_operation_gives_up(self) -> None:
        """A start operation that failed shows gave_up with start_failed."""
        rig = self.submitted()
        rig.lane.states["op-1"] = OperationState.FAILED
        rig.at(20)
        self.assertEqual(rig.flow.read(), CrashRestartState("gave_up", "start_failed", WALL_TEXT))

    def test_the_restarted_launch_is_reported(self) -> None:
        """A new launch after the submit shows restarted and logs the operation and the trigger."""
        rig = self.submitted()
        rig.running(token="tok-b", handle="h-b", ready=False)
        rig.at(20)
        self.assertEqual(rig.flow.read(), CrashRestartState("restarted", None, WALL_TEXT))
        self.assertEqual(rig.logger.events(), ["server_crash.restart_pending", "server_crash.restarted"])
        self.assertEqual(rig.logger.records[1][1]["fields"],
                         {"profile_id": "livonia-main", "operation_id": "op-1", "trigger": "crash_restart"})

    def test_a_restart_that_dies_before_ready_gives_up(self) -> None:
        """A restarted launch that ends before READY is given up on, and not restarted again."""
        rig = self.submitted()
        rig.running(token="tok-b", handle="h-b", ready=False)
        rig.at(20)
        rig.ended()
        rig.at(25)
        self.assertEqual(rig.flow.read(), CrashRestartState("gave_up", "ended_before_ready", WALL_TEXT))
        rig.at(45)
        self.assertEqual(rig.lane.submits, [("livonia-main",)])


if __name__ == "__main__":
    unittest.main()
