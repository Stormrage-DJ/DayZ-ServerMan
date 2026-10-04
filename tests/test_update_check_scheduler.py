"""Update-check scheduler: start-time check, single run, wake signals and stop."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.update_check_scheduler import (  # noqa: E402
    UpdateCheckScheduler,
)
from dayz_serverman.composition import build_composition  # noqa: E402
from tests.update_check_fixtures import Harness  # noqa: E402

# Generous bound for one scheduler reaction on a shared runner
REACTION_SECONDS = 5.0


def wait_until(condition, timeout: float = REACTION_SECONDS) -> bool:
    """Poll the condition; return whether it became true in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class UpdateCheckSchedulerTests(unittest.TestCase):
    """Timer behavior over a service with a fake clock and catalog."""

    def setUp(self) -> None:
        """Build a threaded service and its scheduler; always stop the scheduler."""
        self.harness = Harness(threaded=True)
        self.scheduler = UpdateCheckScheduler(self.harness.service)
        self.addCleanup(self.scheduler.stop)

    def calls(self) -> int:
        """Return how many requests reached the fake catalog."""
        return len(self.harness.catalog.calls)

    def test_start_runs_the_first_check_at_once(self) -> None:
        """The first evaluation is immediate and a second start adds no thread."""
        self.scheduler.start()
        self.scheduler.start()
        self.assertTrue(wait_until(lambda: self.calls() == 1))
        self.assertTrue(self.harness.wait_idle())
        # Nothing is due again until the interval has passed
        self.scheduler.wake()
        time.sleep(0.2)
        self.assertEqual(self.calls(), 1)

    def test_interval_and_wake_start_the_next_check(self) -> None:
        """After the interval a wake signal leads to exactly one more run."""
        self.scheduler.start()
        self.assertTrue(wait_until(lambda: self.calls() == 1))
        self.assertTrue(self.harness.wait_idle())
        self.harness.clock.advance(1800)
        self.scheduler.wake()
        self.assertTrue(wait_until(lambda: self.calls() == 2))

    def test_no_run_while_checking_and_re_evaluation_after_the_run(self) -> None:
        """During a run nothing starts; the end of the run triggers a new evaluation."""
        release = self.harness.catalog.block()
        self.scheduler.start()
        self.assertTrue(self.harness.catalog.entered.wait(REACTION_SECONDS))
        # The clock passes the interval while the run is still active
        self.harness.clock.advance(4000)
        for _ in range(5):
            self.scheduler.wake()
            time.sleep(0.02)
        self.assertEqual(self.calls(), 1)
        # The run fails; its end wakes the scheduler, which honors the backoff
        self.harness.catalog.error = RuntimeError("boom")
        release.set()
        self.assertTrue(self.harness.wait_idle())
        time.sleep(0.2)
        self.assertEqual(self.calls(), 1)
        self.harness.catalog.error = None
        self.harness.clock.advance(60)
        self.scheduler.wake()
        self.assertTrue(wait_until(lambda: self.calls() == 2))

    def test_disabled_preference_blocks_until_the_switch_is_saved(self) -> None:
        """With automatic checks off nothing runs; a wake after enabling starts the check."""
        self.harness.enabled = False
        self.scheduler.start()
        time.sleep(0.3)
        self.assertEqual(self.calls(), 0)
        self.harness.enabled = True
        self.scheduler.wake()
        self.assertTrue(wait_until(lambda: self.calls() == 1))

    def test_stop_returns_within_two_seconds_even_during_a_run(self) -> None:
        """Stop signals the timer and joins it; a blocked run does not hold it."""
        release = self.harness.catalog.block()
        self.scheduler.start()
        self.assertTrue(self.harness.catalog.entered.wait(REACTION_SECONDS))
        before = time.monotonic()
        self.assertTrue(self.scheduler.stop())
        self.assertLess(time.monotonic() - before, 2.0)
        self.assertTrue(self.scheduler.stop())
        release.set()
        self.assertTrue(self.harness.wait_idle())
        # A stopped scheduler starts no further check
        self.harness.clock.advance(4000)
        self.scheduler.wake()
        time.sleep(0.2)
        self.assertEqual(self.calls(), 1)

    def test_failing_evaluation_does_not_end_the_timer(self) -> None:
        """An error in one evaluation is survived; the next wake evaluates again."""
        original = self.harness.service.run_scheduled
        failures = [RuntimeError("evaluation")]

        def flaky() -> bool:
            """Fail the first evaluation, then behave normally."""
            if failures:
                raise failures.pop()
            return original()

        self.harness.service.run_scheduled = flaky
        self.scheduler.start()
        self.assertTrue(wait_until(lambda: not failures))
        self.scheduler.wake()
        self.assertTrue(wait_until(lambda: self.calls() == 1))


class ComposedSchedulerTests(unittest.TestCase):
    """The production wiring of the timer and the preference switch."""

    def test_saving_the_preference_wakes_the_scheduler(self) -> None:
        """The composed scheduler is woken by save_automatic_update_checks."""
        with tempfile.TemporaryDirectory(prefix="serverman_scheduler_") as temporary:
            composition = build_composition(Path(temporary) / "Manager")
            try:
                woken = threading.Event()
                with patch.object(composition.update_check_scheduler, "_wake", woken):
                    result = composition.bridge.dispatch({
                        "contract_version": 1, "request_id": "wake-1",
                        "method": "save_automatic_update_checks",
                        "parameters": {"enabled": False},
                    })
                self.assertTrue(result["success"])
                self.assertTrue(woken.is_set())
                self.assertIs(composition.preferences.automatic_update_checks(), False)
                # The switch reaches the service: the scheduled trigger now refuses
                self.assertFalse(composition.update_check.run_scheduled())
            finally:
                composition.operations.shutdown(2)


if __name__ == "__main__":
    unittest.main()
