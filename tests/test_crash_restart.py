"""Crash restart flow (plan 2.1): detection of the end of a READY launch."""

from __future__ import annotations

import unittest

from tests.crash_restart_support import WALL_TEXT, Rig

from dayz_serverman.application.crash_restart_flow import CrashRestartState
from dayz_serverman.application.lifecycle_ownership import LaunchFacts


class DetectionTests(unittest.TestCase):
    """Timelines are typed by hand: the running poll is at 0 s and the end is seen at 5 s."""

    def setUp(self) -> None:
        """Start with a fresh rig."""
        self.rig = Rig()

    def test_a_ready_launch_that_ends_becomes_pending(self) -> None:
        """The end is claimed once, the handle is released and the restart is pending."""
        self.rig.running()
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)
        self.assertEqual(self.rig.flow.read(), CrashRestartState("pending", None, WALL_TEXT))
        self.assertEqual(self.rig.lifecycle.released, ["h-a"])
        self.assertEqual(self.rig.ownership.launch_facts("tok-a"), LaunchFacts(False, True, True))
        self.assertEqual(self.rig.logger.events(), ["server_crash.restart_pending"])
        self.assertEqual(self.rig.logger.records[0][1]["fields"],
                         {"profile_id": "livonia-main", "wait_seconds": 10.0})
        self.assertEqual(self.rig.lane.submits, [])

    def test_a_stop_intent_is_never_a_crash(self) -> None:
        """A manager stop is not a crash."""
        self.rig.running()
        self.rig.at(0)
        self.rig.ownership.mark_stop_intent("tok-a")
        self.rig.ended()
        self.rig.at(5)
        self.assertIsNone(self.rig.flow.read())
        self.assertEqual(self.rig.lifecycle.released, [])
        self.assertEqual(self.rig.logger.records, [])

    def test_an_end_before_ready_is_never_a_crash(self) -> None:
        """A launch that never became READY is not restarted."""
        self.rig.running(ready=False)
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)
        self.assertIsNone(self.rig.flow.read())
        self.assertEqual(self.rig.lifecycle.released, [])
        self.assertEqual(self.rig.logger.records, [])

    def test_an_adopted_launch_is_a_crash_too(self) -> None:
        """An adopted launch has no handle: it is pending and nothing is released."""
        self.rig.running(handle=None)
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)
        self.assertEqual(self.rig.flow.read(), CrashRestartState("pending", None, WALL_TEXT))
        self.assertEqual(self.rig.lifecycle.released, [])

    def test_the_guard_gives_up_the_fourth_crash(self) -> None:
        """Three crash restarts in the last 10 minutes refuse the next one."""
        self.rig.flow._restart_times = [100.0, 200.0, 300.0]
        self.rig.running()
        self.rig.at(380)
        self.rig.ended()
        self.rig.at(400)
        self.assertEqual(self.rig.flow.read(), CrashRestartState("gave_up", "guard_reached", WALL_TEXT))
        self.assertEqual(self.rig.logger.events(), ["server_crash.gave_up"])

    def test_a_launch_without_a_known_profile_is_not_claimed(self) -> None:
        """A launch that was never seen with a profile gets no claim and no restart."""
        self.rig.running(profile=None)
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)
        self.assertEqual(self.rig.ownership.launch_facts("tok-a"), LaunchFacts(False, True, False))
        self.assertIsNone(self.rig.flow.read())

    def test_a_second_poll_claims_nothing_again(self) -> None:
        """The same end seen at 5 s and at 10 s gives one release and one record."""
        self.rig.running()
        self.rig.at(0)
        self.rig.ended()
        self.rig.at(5)
        self.rig.at(10)
        self.assertEqual(self.rig.lifecycle.released, ["h-a"])
        self.assertEqual(self.rig.logger.events(), ["server_crash.restart_pending"])


if __name__ == "__main__":
    unittest.main()
