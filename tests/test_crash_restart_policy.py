"""Crash policy (plan 1.2): which ended launches are crashes, the loop guard and the restart timing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runnable" / "src" / "python"))

from dayz_serverman.application.crash_restart_policy import (  # noqa: E402
    CrashAction, CrashDecision, CrashReason, decide_crash, guard_refusal, is_crash,
)
from dayz_serverman.application.lifecycle_ownership import LaunchFacts  # noqa: E402


class ClassificationTests(unittest.TestCase):
    """Only a READY launch that ended without a stop and without a claim is a crash."""

    def test_decision_table_of_an_ended_launch(self) -> None:
        """Each row is (stopped_with_evidence, ready, stop_intent, end_claimed, expected)."""
        rows = [
            # Criterion 1: a READY server that ended without a manager stop
            (True, True, False, False, True),
            # Criterion 5: a stop on purpose
            (True, True, True, False, False),
            # Criterion 6: an end before READY
            (True, False, False, False, False),
            # The end was already claimed
            (True, True, False, True, False),
            # The server still runs or no evidence is present
            (False, True, False, False, False),
        ]
        for row in rows:
            with self.subTest(row=row):
                stopped, ready, stop_intent, end_claimed, expected = row
                self.assertEqual(is_crash(LaunchFacts(stop_intent, ready, end_claimed), stopped), expected)


class GuardTests(unittest.TestCase):
    """The loop guard refuses the crash after 3 restarts inside the last 600 seconds."""

    def test_guard_table(self) -> None:
        """Each row is (restart_times, now, expected), from criterion 4."""
        rows = [
            # No earlier restart
            ([], 100.0, None),
            # 2 restarts inside the window
            ([0.0, 100.0], 200.0, None),
            # 3 inside, the 4th crash is refused
            ([0.0, 100.0, 200.0], 300.0, CrashReason.GUARD_REACHED),
            # 3 restarts older than 10 minutes, all outside (100.0, 700.0]
            ([0.0, 10.0, 20.0], 700.0, None),
            # The restart at 0.0 is exactly 600.0 s old and no longer counts
            ([0.0, 100.0, 200.0], 600.0, None),
            # 599.0 s old still counts
            ([1.0, 100.0, 200.0], 600.0, CrashReason.GUARD_REACHED),
            # The list order does not matter
            ([200.0, 0.0, 100.0], 300.0, CrashReason.GUARD_REACHED),
        ]
        for row in rows:
            with self.subTest(row=row):
                restart_times, now, expected = row
                self.assertEqual(guard_refusal(restart_times, now), expected)

    def test_a_fourth_crash_is_refused_in_a_sequence(self) -> None:
        """Three crashes restart; the fourth is refused until the first three are 600 s old."""
        times: list[float] = []
        for crash_time in (0.0, 60.0, 120.0):
            self.assertIsNone(guard_refusal(times, crash_time))
            times.append(crash_time)
        self.assertEqual(guard_refusal(times, 180.0), CrashReason.GUARD_REACHED)
        self.assertIsNone(guard_refusal(times, 721.0))


class DecisionTests(unittest.TestCase):
    """A crash gives wait, restart or give up, in that priority: the guard first."""

    def test_decision_table(self) -> None:
        """Each row is (restart_times, now, detected_at, expected), from criteria 1 and 4."""
        rows = [
            # Just detected
            ([], 100.0, 100.0, CrashDecision(CrashAction.WAIT, None)),
            # Still inside the 10 s wait
            ([], 109.9, 100.0, CrashDecision(CrashAction.WAIT, None)),
            # Exactly 10.0 s
            ([], 110.0, 100.0, CrashDecision(CrashAction.RESTART, None)),
            # After the wait
            ([], 125.0, 100.0, CrashDecision(CrashAction.RESTART, None)),
            # The guard refuses at once, no wait
            ([0.0, 50.0, 100.0], 110.0, 100.0, CrashDecision(CrashAction.GIVE_UP, CrashReason.GUARD_REACHED)),
        ]
        for row in rows:
            with self.subTest(row=row):
                restart_times, now, detected_at, expected = row
                self.assertEqual(decide_crash(restart_times, now, detected_at), expected)

    def test_the_guard_wins_over_the_wait_and_over_the_restart(self) -> None:
        """With 3 restarts in the window the result is give up, whether the wait is over or not."""
        give_up = CrashDecision(CrashAction.GIVE_UP, CrashReason.GUARD_REACHED)
        for now in (100.0, 200.0):
            with self.subTest(now=now):
                self.assertEqual(decide_crash([10.0, 20.0, 30.0], now, 100.0), give_up)


if __name__ == "__main__":
    unittest.main()
