"""A181 tests 12 and 14: changes between passes, job lifetime, eviction, concurrency and retired generations."""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from dayz_serverman.adapters.windows.shared_files import replace_file
from dayz_serverman.application.mission_map_inspection_jobs import InspectionJobs, InspectionUnknown
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.mission_map_inspection_support import InspectionFixture, ManualClock, mission_bytes
from tests.test_mission_map_plan import circle
from tests.test_mission_map_plan_service import profile


# Longest wait for a helper thread of these tests
JOIN_SECONDS = 5.0


def reason(action) -> str:
    """Return the refusal reason of an action."""
    try:
        action()
    except ApplicationCallError as error:
        return error.details["reason"]
    raise AssertionError("the action did not refuse")


class ChangeBetweenPassesTests(InspectionFixture):
    """A181 test 12 through the service: mission changes between capture and proof and between proof slices."""

    def setUp(self) -> None:
        """Save one plan with a large circle."""
        super().setUp()
        self.saved([circle("c1", x=500.0, z=500.0, radius=400.0)])

    def run_to_proof(self) -> dict:
        """Start an object read and continue until the proof pass is the next step."""
        answer = self.start({"object": "c1"})
        run = self.jobs._jobs[answer["job"]["id"]].run
        while run.stage != "proof":
            answer = self.proceed(answer["job"]["id"])
        return answer

    def replace(self, name: str, data: bytes) -> None:
        """Replace one mission file the way a D4 publication does."""
        staged = self.mission / f".{name}.stage"
        staged.write_bytes(data)
        replace_file(staged, self.mission / name)

    def test_12_changes_between_capture_and_proof_give_stale(self) -> None:
        """A new areaflags.map and a limits-only change each end the job stale."""
        changes = {
            "areaflags content": ("areaflags.map", mission_bytes(bits=8)),
            "limits only": ("cfglimitsdefinition.xml", b"<lists><valueflags/></lists>"),
        }
        for name, (file_name, data) in changes.items():
            with self.subTest(name):
                answer = self.run_to_proof()
                before = (self.mission / file_name).read_bytes()
                self.replace(file_name, data)
                answer, _phases = self.finish(answer)
                self.assertEqual(answer["status"], "stale")
                self.replace(file_name, before)

    def test_12_a_change_between_two_proof_slices_gives_stale(self) -> None:
        """The proof read areaflags.map in its first slice; its replacement is found by the next slice."""
        answer = self.run_to_proof()
        run = self.jobs._jobs[answer["job"]["id"]].run
        answer = self.proceed(answer["job"]["id"])
        self.assertEqual((answer["status"], run._pass.position), ("running", (1, 0)))
        # Identical bytes in a new file: only the file ID differs
        self.replace("areaflags.map", (self.mission / "areaflags.map").read_bytes())
        self.assertEqual(self.proceed(answer["job"]["id"])["status"], "stale")

    def test_a_profile_change_ends_stale_and_a_lost_profile_refuses(self) -> None:
        """The trusted context is rechecked in each reader-held slice."""
        original = self.profiles.records["main"]
        moved = self.start({"object": "c1"})
        self.profiles.records["main"] = profile("main", r"mpmissions\dayzOffline.other", "profile_main")
        self.assertEqual(self.proceed(moved["job"]["id"])["status"], "stale")
        self.profiles.records["main"] = original
        lost = self.start({"object": "c1"})
        with mock.patch.object(self.profiles, "read", side_effect=ProfileNotFound("gone")):
            self.assertEqual(reason(lambda: self.proceed(lost["job"]["id"])), "profile_not_found")
        self.assertEqual(reason(lambda: self.proceed(lost["job"]["id"])), "inspection_unknown")


class FakeRun:
    """A run whose advance waits for the test, then answers running."""

    def __init__(self) -> None:
        """Prepare the gate events."""
        self.entered, self.release = threading.Event(), threading.Event()

    def advance(self) -> dict:
        """Wait for release, then return running progress."""
        self.entered.set()
        self.release.wait(JOIN_SECONDS)
        return {"status": "running", "phase": "sampling", "rows_done": 7, "rows_total": 9}


class JobLifetimeTests(InspectionFixture):
    """A181 test 14: cancel, a newer start, expiry and the fifth job each give inspection_unknown."""

    def setUp(self) -> None:
        """Save one plan with a large circle."""
        super().setUp()
        self.saved([circle("c1", x=500.0, z=500.0, radius=400.0)])

    def running(self) -> str:
        """Start an object read that stays running and return its job ID."""
        answer = self.start({"object": "c1"})
        self.assertEqual(answer["status"], "running")
        return answer["job"]["id"]

    def test_14_cancel_newer_start_and_expiry_retire_the_job(self) -> None:
        """Each retired token refuses continue and cancel with inspection_unknown."""
        cancelled = self.running()
        self.assertEqual(self.cancel(cancelled), {"cancelled": True})
        replaced = self.running()
        newest = self.running()
        self.registry_clock.now += 61
        for job_id in (cancelled, replaced, newest, "never-issued"):
            with self.subTest(job_id=job_id):
                self.assertEqual(reason(lambda: self.proceed(job_id)), "inspection_unknown")
                self.assertEqual(reason(lambda: self.cancel(job_id)), "inspection_unknown")
        # Another profile cannot address a live job
        live = self.running()
        self.assertEqual(reason(lambda: self.proceed(live, profile_id="second")), "inspection_unknown")
        self.registry_clock.now += 59
        self.assertEqual(self.proceed(live)["status"], "running")

    def test_14_the_fifth_job_evicts_the_oldest_creation(self) -> None:
        """Four live jobs per session; the fifth start retires the oldest one."""
        jobs = InspectionJobs(clock=ManualClock())
        created = [jobs.create(f"profile-{index}", FakeRun(), lambda job_id: {"job": job_id}) for index in range(5)]
        self.assertEqual(jobs.live(), 4)
        self.assertTrue(created[0].retired)
        with self.assertRaises(InspectionUnknown):
            jobs.claim("profile-0", created[0].job_id)
        for job in created[1:]:
            self.assertFalse(job.retired)

    def test_14_a_simultaneous_continue_returns_the_last_progress_and_a_retired_job_never_publishes(self) -> None:
        """While one call advances, a second continue answers the last progress; cancel then wins."""
        job_id = self.running()
        job = self.jobs._jobs[job_id]
        last = dict(job.progress)
        job.run = FakeRun()
        outcome: dict = {}

        def advance() -> None:
            """Advance in a second thread and keep its answer or its refusal reason."""
            try:
                outcome["answer"] = self.proceed(job_id)
            except ApplicationCallError as error:
                outcome["reason"] = error.details["reason"]
        thread = threading.Thread(target=advance)
        thread.start()
        self.assertTrue(job.run.entered.wait(JOIN_SECONDS))
        self.assertEqual(self.proceed(job_id), last)
        self.assertEqual(self.cancel(job_id), {"cancelled": True})
        job.run.release.set()
        thread.join(JOIN_SECONDS)
        self.assertEqual(outcome, {"reason": "inspection_unknown"})
        self.assertEqual(reason(lambda: self.proceed(job_id)), "inspection_unknown")


if __name__ == "__main__":
    unittest.main()
