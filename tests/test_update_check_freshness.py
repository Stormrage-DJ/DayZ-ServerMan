"""Freshness of the synchronous check: no clock value and no cached time can make a fact fresh."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.domain.update_check import (  # noqa: E402
    RemoteBatchFailure as Failure,
    RemoteFact,
    RemoteItemResult as Result,
    UpdateCheckRecord,
)
from dayz_serverman.domain.update_check_rules import utc_text  # noqa: E402
from dayz_serverman.repositories.update_check_cache import (  # noqa: E402
    MAX_FUTURE_SECONDS,
    UpdateCheckCacheRepository,
)
from tests.update_check_fixtures import START, Harness  # noqa: E402

# A fact time far ahead of every clock the tests use
FUTURE = "2099-01-01T00:00:00.000+00:00"


def cache_document(checked_at: str, attempt_at: str | None = None,
                   success_at: str | None = None) -> dict[str, object]:
    """Return a well-formed cache document whose two facts carry the given time."""
    attempt = None if attempt_at is None else {
        "finished_at": attempt_at, "outcome": "OK", "error_code": None}
    return {
        "schema_version": 1, "last_attempt": attempt, "last_success_at": success_at,
        "items": {key: {"result": "OK", "time_updated": 100, "file_size": 5,
                        "checked_at": checked_at} for key in ("111", "222")},
    }


class CheckNowFreshnessTests(unittest.TestCase):
    """check_now returns only facts that a run answered during the call."""

    def test_backward_clock_step_then_failed_run_gives_no_entry(self) -> None:
        """Facts of an earlier run stay excluded when the wall clock was set back."""
        harness = Harness(ids=("111", "222"))
        self.assertEqual(set(harness.service.check_now(("111", "222"))), {"111", "222"})
        # The clock is corrected backwards by one hour; Steam is then unreachable
        harness.clock.advance(-3600)
        harness.catalog.failures = [Failure.NETWORK_UNREACHABLE]
        self.assertEqual(harness.service.check_now(("111", "222")), {})
        # The earlier facts are still known to the status view
        self.assertEqual(set(harness.service.snapshot().facts), {"111", "222"})

    def test_future_dated_cached_fact_then_failed_run_gives_no_entry(self) -> None:
        """A cached fact with a future time is never returned, whatever the loader accepted."""
        record = UpdateCheckRecord(None, None, {
            key: RemoteFact(Result.OK, 100, 5, FUTURE) for key in ("111", "222")})
        harness = Harness(ids=("111", "222"), record=record)
        harness.catalog.failures = [Failure.NETWORK_UNREACHABLE]
        self.assertEqual(harness.service.check_now(("111", "222")), {})

    def test_cached_fact_is_not_fresh_when_the_run_omits_the_item(self) -> None:
        """A successful run that does not answer an id leaves its cached fact excluded."""
        record = UpdateCheckRecord(None, None, {"111": RemoteFact(Result.OK, 100, 5, FUTURE)})
        harness = Harness(ids=("111", "222"), record=record)
        # The answer of the run names only the other id
        original = harness.catalog.fetch
        harness.catalog.fetch = lambda ids, deadline: original(("222",), deadline)
        self.assertEqual(set(harness.service.check_now(("111", "222"))), {"222"})

    def test_failed_run_returns_only_the_batches_it_answered(self) -> None:
        """Ids of the failed batch get no entry; the answered batch of this call counts."""
        ids = [str(1000 + index) for index in range(250)]
        harness = Harness(ids=ids)
        harness.service.check_now(ids)
        harness.catalog.failures = [None, Failure.TIMEOUT]
        facts = harness.service.check_now(ids)
        self.assertEqual(set(facts), set(ids[:200]))

    def test_joined_batch_answered_before_the_call_gives_no_entry(self) -> None:
        """A joined run counts only for the batches that answered after the call began."""
        ids = [str(1000 + index) for index in range(250)]
        harness = Harness(ids=ids, threaded=True)
        second_entered, release = threading.Event(), threading.Event()
        original = harness.catalog.fetch

        def fetch(batch, deadline):
            """Answer the first batch at once and hold the second one."""
            if batch[0] != ids[0]:
                second_entered.set()
                release.wait(10)
            return original(batch, deadline)

        harness.catalog.fetch = fetch
        harness.service.request(True)
        self.assertTrue(second_entered.wait(5))
        # The call begins after the first batch answered and joins the same run
        joined: list[dict] = []
        caller = threading.Thread(target=lambda: joined.append(harness.service.check_now(ids)))
        caller.start()
        caller.join(0.2)
        self.assertTrue(caller.is_alive())
        release.set()
        caller.join(5)
        self.assertEqual(set(joined[0]), set(ids[200:]))
        self.assertEqual(len(harness.catalog.calls), 2)


class FutureTimeCacheTests(unittest.TestCase):
    """The cache loader treats a file with a time from the future as "never checked"."""

    def setUp(self) -> None:
        """Create a cache file path and a repository with a fixed clock."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "update-check.json"
        self.repository = UpdateCheckCacheRepository(self.path, clock=lambda: START)

    def load(self, document: dict[str, object]):
        """Store the document and return what the repository loads."""
        self.path.write_text(json.dumps(document), encoding="utf-8")
        return self.repository.load()

    def test_future_fact_attempt_or_success_time_rejects_the_file(self) -> None:
        """Each stored time is checked against the clock."""
        now = utc_text(START)
        self.assertIsNotNone(self.load(cache_document(now, now, now)))
        for name, document in {
            "fact": cache_document(FUTURE, now, now),
            "attempt": cache_document(now, FUTURE, now),
            "success": cache_document(now, now, FUTURE),
        }.items():
            with self.subTest(name=name):
                self.assertIsNone(self.load(document))

    def test_only_the_small_allowance_is_accepted(self) -> None:
        """A time inside the allowance loads; one second beyond it does not."""
        inside = utc_text(START + timedelta(seconds=MAX_FUTURE_SECONDS))
        beyond = utc_text(START + timedelta(seconds=MAX_FUTURE_SECONDS + 1))
        self.assertIsNotNone(self.load(cache_document(inside)))
        self.assertIsNone(self.load(cache_document(beyond)))

    def test_future_dated_file_with_failing_transport_gives_no_entry(self) -> None:
        """The hand-edited file is ignored and a failed run then returns nothing."""
        self.path.write_text(json.dumps(cache_document(FUTURE)), encoding="utf-8")
        harness = Harness(ids=("111", "222"), cache=self.repository)
        self.assertEqual(harness.service.snapshot().facts, {})
        harness.catalog.failures = [Failure.NETWORK_UNREACHABLE]
        self.assertEqual(harness.service.check_now(("111", "222")), {})


if __name__ == "__main__":
    unittest.main()
