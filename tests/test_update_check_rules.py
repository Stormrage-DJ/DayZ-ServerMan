"""Pure update-check rules: check state, backoff, next due and the three triggers."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain import update_check_rules as rules  # noqa: E402
from dayz_serverman.domain.update_check import AttemptOutcome, CheckAttempt  # noqa: E402

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
INTERVAL = rules.DEFAULT_INTERVAL_SECONDS


def at(seconds_ago: float) -> str:
    """Return the stored text of a moment the given seconds before NOW."""
    return rules.utc_text(NOW - timedelta(seconds=seconds_ago))


def attempt(seconds_ago: float, ok: bool = True) -> CheckAttempt:
    """Return an attempt that ended the given seconds before NOW."""
    if ok:
        return CheckAttempt(at(seconds_ago), AttemptOutcome.OK, None)
    return CheckAttempt(at(seconds_ago), AttemptOutcome.FAILED, "TIMEOUT")


class CheckStateTests(unittest.TestCase):
    """One case per row of the check-state table."""

    def test_no_attempt_is_never(self) -> None:
        """Without a recorded attempt the state is NEVER."""
        self.assertEqual(rules.check_state(None, None, NOW, INTERVAL), rules.CheckState.NEVER)

    def test_failed_attempt_is_failed_even_with_an_older_success(self) -> None:
        """A failed last attempt reports FAILED and keeps the success time."""
        state = rules.check_state(attempt(10, ok=False), at(100), NOW, INTERVAL)
        self.assertEqual(state, rules.CheckState.FAILED)

    def test_success_within_two_intervals_is_ok(self) -> None:
        """A success no older than the stale-after bound is OK, the bound included."""
        for age in (0, 60, 2 * INTERVAL):
            state = rules.check_state(attempt(age), at(age), NOW, INTERVAL)
            self.assertEqual(state, rules.CheckState.OK, age)

    def test_older_success_or_backward_clock_is_stale(self) -> None:
        """A success above the bound, or one in the future, is STALE."""
        for age in (2 * INTERVAL + 1, -5):
            state = rules.check_state(attempt(age), at(age), NOW, INTERVAL)
            self.assertEqual(state, rules.CheckState.STALE, age)

    def test_timestamps_have_the_cache_form(self) -> None:
        """Written times are UTC text with milliseconds and parse back equal."""
        self.assertEqual(rules.utc_text(NOW), "2026-10-03T12:00:00.000+00:00")
        self.assertEqual(rules.parse_utc(rules.utc_text(NOW)), NOW)
        self.assertIsNone(rules.parse_utc("yesterday"))


class BackoffAndDueTests(unittest.TestCase):
    """Backoff steps, the cap and the next-due moment."""

    def test_backoff_doubles_from_sixty_seconds_up_to_the_interval(self) -> None:
        """Failures 1, 2, 3 wait 60, 120, 240 seconds; the interval caps the wait."""
        waits = [rules.backoff_seconds(count, INTERVAL) for count in (1, 2, 3)]
        self.assertEqual(waits, [60, 120, 240])
        self.assertEqual(rules.backoff_seconds(6, INTERVAL), INTERVAL)
        self.assertEqual(rules.backoff_seconds(500, INTERVAL), INTERVAL)
        self.assertEqual(rules.backoff_seconds(0, INTERVAL), 0)

    def test_interval_outside_fifteen_to_sixty_minutes_is_rejected(self) -> None:
        """The constructor value must lie in the accepted range."""
        for value in (14 * 60, 61 * 60, True, "1800"):
            with self.assertRaises(ValueError):
                rules.validate_interval(value)
        self.assertEqual(rules.validate_interval(15 * 60), 900.0)

    def test_next_due_follows_never_success_and_failure(self) -> None:
        """Due now when never checked; one interval after success; backoff after failure."""
        self.assertEqual(rules.next_due(None, 0, NOW, INTERVAL), NOW)
        self.assertEqual(
            rules.next_due(attempt(100), 0, NOW, INTERVAL),
            NOW + timedelta(seconds=INTERVAL - 100),
        )
        self.assertEqual(
            rules.next_due(attempt(10, ok=False), 2, NOW, INTERVAL),
            NOW + timedelta(seconds=110),
        )
        # An attempt time in the future (clock moved back) is due at once
        self.assertEqual(rules.next_due(attempt(-50), 0, NOW, INTERVAL), NOW)


class TriggerTests(unittest.TestCase):
    """Each trigger row, accepted and refused."""

    def scheduled(self, **changes: object) -> bool:
        """Evaluate the scheduler trigger with defaults that allow a run."""
        values = {"enabled": True, "checking": False, "attempt": None, "failures": 0,
                  "now": NOW, "interval_seconds": INTERVAL}
        return rules.scheduled_run_allowed(**{**values, **changes})

    def requested(self, **changes: object) -> bool:
        """Evaluate the non-forced trigger with defaults that allow a run."""
        values = {"enabled": True, "checking": False, "attempt": attempt(300),
                  "failures": 0, "now": NOW, "interval_seconds": INTERVAL,
                  "fact_missing": False}
        return rules.requested_run_allowed(**{**values, **changes})

    def test_scheduled_trigger(self) -> None:
        """The scheduler runs when enabled, idle and due."""
        self.assertTrue(self.scheduled())
        self.assertTrue(self.scheduled(attempt=attempt(INTERVAL)))
        self.assertFalse(self.scheduled(enabled=False))
        self.assertFalse(self.scheduled(checking=True))
        self.assertFalse(self.scheduled(attempt=attempt(INTERVAL - 1)))
        # After one failure the next run waits 60 seconds
        self.assertFalse(self.scheduled(attempt=attempt(59, ok=False), failures=1))
        self.assertTrue(self.scheduled(attempt=attempt(60, ok=False), failures=1))

    def test_non_forced_request_trigger(self) -> None:
        """A request runs when old enough or a fact is missing, outside backoff."""
        self.assertTrue(self.requested())
        self.assertTrue(self.requested(attempt=None))
        self.assertTrue(self.requested(attempt=attempt(10), fact_missing=True))
        self.assertFalse(self.requested(attempt=attempt(299)))
        self.assertFalse(self.requested(enabled=False))
        self.assertFalse(self.requested(checking=True))
        # Backoff refuses even an old enough failed attempt with a missing fact
        failed = attempt(400, ok=False)
        self.assertFalse(self.requested(attempt=failed, failures=4, fact_missing=True))
        self.assertTrue(self.requested(attempt=failed, failures=3))

    def test_forced_trigger_ignores_switch_and_backoff_but_is_debounced(self) -> None:
        """"Check now" needs only an idle service and a 5 second gap."""
        allowed = rules.forced_run_allowed
        self.assertTrue(allowed(checking=False, attempt=None, now=NOW))
        self.assertTrue(allowed(checking=False, attempt=attempt(5, ok=False), now=NOW))
        self.assertFalse(allowed(checking=False, attempt=attempt(4), now=NOW))
        self.assertFalse(allowed(checking=True, attempt=None, now=NOW))


if __name__ == "__main__":
    unittest.main()
