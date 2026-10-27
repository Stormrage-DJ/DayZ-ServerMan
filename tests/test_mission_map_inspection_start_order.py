"""Inspect Start Order (Architect 2026-10-14 01:26:20): only the newest Start of a profile creates a job."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager

from dayz_serverman.application.mission_map_inspection_jobs import InspectionJobs, InspectionUnknown
from tests.mission_map_inspection_support import InspectionFixture, ManualClock, RecordingReader
from tests.test_mission_map_inspection_jobs import FakeRun, reason
from tests.test_mission_map_plan import circle


# Longest wait for the helper thread of these tests
JOIN_SECONDS = 10.0


class GatedReader(RecordingReader):
    """A reader whose first hold waits for the test, so the first Start ends after a later one."""

    def __init__(self) -> None:
        """Prepare the gate events of the first hold."""
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()
        self.gated = True

    @contextmanager
    def hold(self, bound_seconds: float) -> Iterator[None]:
        """Wait in the first hold until the test releases it; every later hold acts as the recording reader."""
        if self.gated:
            self.gated = False
            self.entered.set()
            self.release.wait(JOIN_SECONDS)
        with super().hold(bound_seconds):
            yield


class StartOrderTests(InspectionFixture):
    """An older Start never replaces the job of a newer one; the newer Start still replaces an older job."""

    def setUp(self) -> None:
        """Save one plan with a large circle and gate the first reader hold."""
        super().setUp()
        self.saved([circle("c1", x=500.0, z=500.0, radius=400.0)])
        self.reader = GatedReader()
        self.inspection._reader = self.reader

    def test_an_older_start_that_ends_last_creates_no_job(self) -> None:
        """The older Start gets inspection_unknown; the newer job stays live and continues."""
        outcome: dict = {}

        def older() -> None:
            """Run the first Start; it waits in its context step."""
            outcome["reason"] = reason(lambda: self.start({"object": "c1"}))

        thread = threading.Thread(target=older)
        thread.start()
        self.assertTrue(self.reader.entered.wait(JOIN_SECONDS))
        newer = self.start({"object": "c1"})
        self.assertEqual(newer["status"], "running")
        self.reader.release.set()
        thread.join(JOIN_SECONDS)
        self.assertEqual(outcome["reason"], "inspection_unknown")
        self.assertEqual(self.jobs.live(), 1)
        self.assertIn(self.proceed(newer["job"]["id"])["status"], ("running", "done"))

    def test_an_older_start_that_ends_first_is_replaced(self) -> None:
        """Without overlap the newer Start replaces the job of the older one, as in A181 test 14."""
        self.reader.gated = False
        older = self.start({"object": "c1"})
        newer = self.start({"object": "c1"})
        self.assertEqual(reason(lambda: self.proceed(older["job"]["id"])), "inspection_unknown")
        self.assertIn(self.proceed(newer["job"]["id"])["status"], ("running", "done"))

    def test_the_registry_creates_only_the_newest_arrival(self) -> None:
        """A stale arrival retires nothing; other profiles and the four-job limit are unchanged."""
        jobs = InspectionJobs(clock=ManualClock())
        first, second = jobs.arrive("alpha"), jobs.arrive("alpha")
        other = jobs.arrive("bravo")
        kept = jobs.create("bravo", FakeRun(), lambda job_id: {"job": job_id}, other)
        newest = jobs.create("alpha", FakeRun(), lambda job_id: {"job": job_id}, second)
        with self.assertRaises(InspectionUnknown):
            jobs.create("alpha", FakeRun(), lambda job_id: {"job": job_id}, first)
        self.assertFalse(newest.retired or kept.retired)
        self.assertEqual(jobs.live(), 2)
        # A job without an arrival number keeps the earlier rule, so the limit and eviction stay as they were
        created = [jobs.create(f"profile-{index}", FakeRun(), lambda job_id: {"job": job_id}) for index in range(3)]
        self.assertEqual(jobs.live(), 4)
        self.assertTrue(kept.retired)
        self.assertFalse(any(job.retired for job in created))


if __name__ == "__main__":
    unittest.main()
