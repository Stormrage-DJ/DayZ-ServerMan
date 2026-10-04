"""Check service: runs, triggers, single-flight, check_now and failure handling."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain.update_check import (  # noqa: E402
    AttemptOutcome,
    RemoteBatchFailure as Failure,
    RemoteItemResult as Result,
)
from dayz_serverman.domain.update_check_rules import CheckState  # noqa: E402
from dayz_serverman.repositories.update_check_cache import (  # noqa: E402
    UpdateCheckCacheRepository,
)
from tests.update_check_fixtures import REMOTE_TIME, Harness  # noqa: E402


class RunTests(unittest.TestCase):
    """What one run sends, stores and reports."""

    def test_successful_run_stores_facts_and_reports_ok(self) -> None:
        """A run over all configured ids ends OK, also with a NOT_FOUND item."""
        harness = Harness(ids=("222", "111", "111"))
        harness.catalog.results["222"] = Result.NOT_FOUND
        self.assertEqual(harness.service.snapshot().check_state, CheckState.NEVER)
        self.assertTrue(harness.service.run_scheduled())
        view = harness.service.snapshot()
        # Ids go out once, in numeric order, within the run deadline
        self.assertEqual(harness.catalog.calls[0][0], ("111", "222"))
        self.assertLessEqual(harness.catalog.calls[0][1], 10)
        self.assertEqual(view.check_state, CheckState.OK)
        self.assertEqual(view.facts["111"].time_updated, REMOTE_TIME)
        self.assertEqual(view.facts["222"].result, Result.NOT_FOUND)
        self.assertIsNone(view.facts["222"].time_updated)
        self.assertEqual((view.error_code, view.checking, view.revision), (None, False, 2))
        self.assertEqual(view.checked_at, view.last_success_at)

    def test_zero_ids_send_nothing_and_are_ok(self) -> None:
        """Without configured ids no request leaves and the run is OK."""
        harness = Harness(ids=())
        self.assertTrue(harness.service.run_scheduled())
        self.assertEqual(harness.catalog.calls, [])
        self.assertEqual(harness.service.snapshot().check_state, CheckState.OK)

    def test_batches_of_two_hundred_and_a_failed_batch_keeps_earlier_facts(self) -> None:
        """450 ids go in three batches; a failure in the second keeps the first."""
        harness = Harness(ids=[str(number) for number in range(1, 451)])
        harness.service.run_scheduled()
        self.assertEqual([len(call[0]) for call in harness.catalog.calls], [200, 200, 50])
        # Fail the second batch of the next run
        harness.clock.advance(3600)
        harness.catalog.calls.clear()
        harness.catalog.failures = [None, Failure.HTTP_STATUS]
        harness.service.run_scheduled()
        view = harness.service.snapshot()
        self.assertEqual(len(harness.catalog.calls), 2)
        self.assertEqual((view.check_state, view.error_code), (CheckState.FAILED, "HTTP_STATUS"))
        # Facts and the last success time survive the failure
        self.assertEqual(len(view.facts), 450)
        self.assertNotEqual(view.facts["1"].checked_at, view.facts["450"].checked_at)
        self.assertLess(view.last_success_at, view.checked_at)

    def test_more_than_one_thousand_ids_are_capped_in_numeric_order(self) -> None:
        """The run covers the numerically first 1,000 ids; the rest get no fact."""
        harness = Harness(ids=[str(number) for number in range(1, 1012)])
        harness.service.run_scheduled()
        facts = harness.service.snapshot().facts
        self.assertEqual(len(facts), 1000)
        self.assertIn("1000", facts)
        self.assertNotIn("1001", facts)

    def test_record_round_trips_through_the_strict_cache(self) -> None:
        """Records the service writes are accepted by the real cache on read."""
        with tempfile.TemporaryDirectory() as temporary:
            cache = UpdateCheckCacheRepository(Path(temporary) / "update-check.json")
            harness = Harness(cache=cache)
            harness.catalog.results["222"] = Result.WRONG_APP
            harness.service.run_scheduled()
            ok = cache.load()
            # A failed run keeps the code beside the FAILED outcome
            harness.clock.advance(3600)
            harness.catalog.failures = [Failure.TIMEOUT]
            harness.service.run_scheduled()
            failed = cache.load()
            # A new service starts from the cached facts
            restarted = Harness(cache=cache).service.snapshot()
        self.assertEqual((ok.last_attempt.outcome, ok.last_attempt.error_code),
                         (AttemptOutcome.OK, None))
        self.assertEqual(set(ok.items), {"111", "222"})
        self.assertEqual((failed.last_attempt.outcome, failed.last_attempt.error_code),
                         (AttemptOutcome.FAILED, "TIMEOUT"))
        self.assertEqual(failed.last_success_at, ok.last_success_at)
        self.assertEqual(set(restarted.facts), {"111", "222"})
        self.assertEqual(restarted.revision, 0)

    def test_facts_of_unconfigured_ids_are_dropped(self) -> None:
        """An id that no profile configures any more loses its fact."""
        harness = Harness()
        harness.service.run_scheduled()
        harness.ids = ["111"]
        harness.clock.advance(3600)
        harness.service.run_scheduled()
        self.assertEqual(set(harness.service.snapshot().facts), {"111"})
        self.assertEqual(harness.cache.saves[-1][1], frozenset({"111"}))

    def test_exception_in_a_run_is_internal_and_clears_running(self) -> None:
        """A raising catalog or profile read records INTERNAL and frees the slot."""
        harness = Harness()
        harness.catalog.error = RuntimeError("boom")
        self.assertTrue(harness.service.run_scheduled())
        view = harness.service.snapshot()
        self.assertEqual((view.check_state, view.error_code, view.checking),
                         (CheckState.FAILED, "INTERNAL", False))
        # A failing profile read behaves the same way
        harness.catalog.error, harness.id_error = None, OSError("profiles")
        harness.clock.advance(120)
        self.assertTrue(harness.service.run_scheduled())
        self.assertEqual(harness.service.snapshot().error_code, "INTERNAL")
        self.assertFalse(harness.service.snapshot().checking)

    def test_worker_that_cannot_start_is_a_failed_attempt(self) -> None:
        """A failing worker start never leaves the running flag set."""
        harness = Harness()
        harness.service._start_worker = lambda _work: (_ for _ in ()).throw(RuntimeError())
        self.assertFalse(harness.service.run_scheduled())
        view = harness.service.snapshot()
        self.assertEqual((view.error_code, view.checking), ("INTERNAL", False))


class TriggerTests(unittest.TestCase):
    """Scheduler, shell and forced triggers against the service state."""

    def test_scheduled_trigger_waits_for_interval_and_backoff(self) -> None:
        """Runs happen when due: after the interval, or after the growing backoff."""
        harness = Harness()
        self.assertEqual(harness.service.seconds_until_due(), 0)
        self.assertTrue(harness.service.run_scheduled())
        self.assertFalse(harness.service.run_scheduled())
        self.assertEqual(harness.service.seconds_until_due(), 1800)
        harness.clock.advance(1800)
        # Three failures in a row wait 60, 120 and 240 seconds
        for wait in (60, 120, 240):
            harness.catalog.failures = [Failure.NETWORK_UNREACHABLE]
            self.assertTrue(harness.service.run_scheduled())
            self.assertEqual(harness.service.seconds_until_due(), wait)
            harness.clock.advance(wait - 1)
            self.assertFalse(harness.service.run_scheduled())
            harness.clock.advance(1)
        # A success resets the failure count
        self.assertTrue(harness.service.run_scheduled())
        harness.clock.advance(1800)
        harness.catalog.failures = [Failure.TIMEOUT]
        harness.service.run_scheduled()
        self.assertEqual(harness.service.seconds_until_due(), 60)

    def test_non_forced_request_accepts_and_refuses(self) -> None:
        """A request runs when never checked, old enough, or a fact is missing."""
        harness = Harness()
        self.assertEqual(harness.service.request(False), (True, False))
        self.assertEqual(harness.service.request(False), (False, False))
        # A newly configured id has no fact: the request runs despite the age
        harness.ids.append("333")
        self.assertEqual(harness.service.request(False), (True, False))
        harness.clock.advance(299)
        self.assertEqual(harness.service.request(False), (False, False))
        harness.clock.advance(1)
        self.assertEqual(harness.service.request(False), (True, False))
        # Backoff after a failure refuses the request
        harness.clock.advance(300)
        harness.catalog.failures = [Failure.TIMEOUT]
        self.assertTrue(harness.service.request(False)[0])
        harness.ids.append("444")
        harness.clock.advance(59)
        self.assertFalse(harness.service.request(False)[0])
        self.assertEqual(len(harness.catalog.calls), 4)

    def test_forced_request_is_debounced_and_ignores_backoff(self) -> None:
        """"Check now" runs during backoff but not within 5 seconds of an attempt."""
        harness = Harness()
        harness.catalog.failures = [Failure.TIMEOUT]
        self.assertEqual(harness.service.request(True), (True, False))
        harness.clock.advance(4)
        self.assertEqual(harness.service.request(True), (False, False))
        harness.clock.advance(1)
        self.assertEqual(harness.service.request(True), (True, False))
        self.assertEqual(harness.service.snapshot().check_state, CheckState.OK)

    def test_disabled_automatic_checks_leave_only_forced_and_synchronous_checks(self) -> None:
        """With the switch off, scheduler and requests do nothing; status ages to STALE."""
        harness = Harness()
        harness.service.run_scheduled()
        for value in (False, "true", None, 1):
            harness.enabled = value
            harness.clock.advance(1800)
            self.assertFalse(harness.service.run_scheduled(), value)
            self.assertEqual(harness.service.request(False), (False, False), value)
        self.assertEqual(harness.service.snapshot().check_state, CheckState.STALE)
        self.assertEqual(len(harness.catalog.calls), 1)
        # An unreadable switch counts as off; "Check now" and check_now still run
        harness.service._automatic_enabled = lambda: (_ for _ in ()).throw(OSError())
        self.assertFalse(harness.service.run_scheduled())
        self.assertEqual(harness.service.request(True), (True, False))
        self.assertEqual(set(harness.service.check_now(["111"])), {"111"})
        self.assertEqual(len(harness.catalog.calls), 3)


class ConcurrencyTests(unittest.TestCase):
    """Single-flight and the synchronous check with real worker threads."""

    def test_two_concurrent_requests_start_one_run(self) -> None:
        """A request during a run starts nothing and reports checking."""
        harness = Harness(threaded=True)
        release = harness.catalog.block()
        results: list[tuple[bool, bool]] = []
        callers = [threading.Thread(target=lambda: results.append(harness.service.request(True)))
                   for _ in range(2)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(5)
        self.assertTrue(harness.catalog.entered.wait(5))
        self.assertEqual(sorted(results), [(False, True), (True, True)])
        self.assertTrue(harness.service.snapshot().checking)
        self.assertEqual(harness.service.seconds_until_due(), float("inf"))
        self.assertFalse(harness.service.run_scheduled())
        release.set()
        self.assertTrue(harness.wait_idle())
        self.assertEqual(len(harness.catalog.calls), 1)

    def test_idle_listener_fires_after_the_run(self) -> None:
        """The service tells its listeners when a run has ended."""
        harness = Harness(threaded=True)
        ended = threading.Event()
        harness.service.add_idle_listener(ended.set)
        harness.service.request(True)
        self.assertTrue(ended.wait(5))
        self.assertFalse(harness.service.snapshot().checking)

    def test_check_now_starts_a_run_and_returns_fresh_facts(self) -> None:
        """The synchronous check adds the caller's ids and skips unknown ones."""
        harness = Harness(threaded=True)
        harness.catalog.results["999"] = Result.NOT_FOUND
        facts = harness.service.check_now(["111", "999", "not-an-id", "555"])
        self.assertEqual(harness.catalog.calls[0][0], ("111", "222", "555", "999"))
        self.assertEqual(set(facts), {"111", "999", "555"})
        self.assertEqual(facts["999"].result, Result.NOT_FOUND)

    def test_check_now_joins_a_running_check(self) -> None:
        """A synchronous call during a run waits for it and starts no second run."""
        harness = Harness(threaded=True)
        release = harness.catalog.block()
        harness.service.request(True)
        self.assertTrue(harness.catalog.entered.wait(5))
        joined: list[dict] = []
        caller = threading.Thread(target=lambda: joined.append(harness.service.check_now(["111"])))
        caller.start()
        caller.join(0.2)
        self.assertTrue(caller.is_alive())
        release.set()
        caller.join(5)
        self.assertEqual(set(joined[0]), {"111"})
        self.assertEqual(len(harness.catalog.calls), 1)

    def test_check_now_timeout_gives_no_entry(self) -> None:
        """When the run outlasts the wait, the call returns nothing."""
        harness = Harness(threaded=True, wait_seconds=0.2)
        release = harness.catalog.block()
        self.assertEqual(harness.service.check_now(["111"]), {})
        release.set()
        self.assertTrue(harness.wait_idle())

    def test_check_now_excludes_stale_and_failed_facts(self) -> None:
        """A fact from before the call is never returned, also after a failed run."""
        harness = Harness()
        harness.service.run_scheduled()
        harness.clock.advance(60)
        harness.catalog.failures = [Failure.TIMEOUT]
        self.assertEqual(harness.service.check_now(["111", "222"]), {})
        self.assertIn("111", harness.service.snapshot().facts)
        # A missing id simply has no entry
        harness.clock.advance(60)
        harness.catalog.results["222"] = Result.OK
        self.assertEqual(set(harness.service.check_now(["111", "777777"])), {"111", "777777"})
        self.assertEqual(harness.service.check_now([]), {})


if __name__ == "__main__":
    unittest.main()
