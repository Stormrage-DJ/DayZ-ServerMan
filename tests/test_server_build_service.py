"""Server build check service: triggers, waiting, the guard, the exit table and what it records."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType

try:
    from tests.server_build_fixtures import (
        OWNER_MARKER, FakeAppInfo, FakeCache, FakeLogger, FakePreflight, library_install, manifest, result,
    )
except ModuleNotFoundError:
    from server_build_fixtures import (
        OWNER_MARKER, FakeAppInfo, FakeCache, FakeLogger, FakePreflight, library_install, manifest, result,
    )

from dayz_serverman.application.server_build import ServerBuildService
from dayz_serverman.application.steamcmd_guard import SteamCmdRunGuard
from dayz_serverman.domain.models import ManagerSettings
from dayz_serverman.domain.server_build import BranchFact, BuildCheckRecord
from dayz_serverman.domain.update_check import AttemptOutcome, CheckAttempt
from dayz_serverman.domain.update_check_rules import utc_text

START = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class ServiceTests(unittest.TestCase):
    """Detailed design 14.5 to 14.7 and Architect changes 1 and 4, with fakes only."""

    def setUp(self) -> None:
        """Build a library installation of the evidence 6.1 shape and the service with fakes."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dayz = library_install(self.root, manifest())
        self.steamcmd: str | None = str(self.root / "SteamCMD")
        self.now = START
        self.moment = 1000.0
        self.drained = True
        self.enabled = True
        self.guard = SteamCmdRunGuard()
        self.app, self.preflight, self.logger = FakeAppInfo(), FakePreflight(), FakeLogger()
        self.idle: list[int] = []
        self.build()

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def build(self, record: BuildCheckRecord | None = None, start_worker=None) -> None:
        """(Re)build the service; the worker runs inline unless a test passes another one."""
        self.cache = FakeCache(record)
        self.service = ServerBuildService(
            self.preflight, self.app, self.cache, self.settings, self.guard, lambda: self.drained,
            automatic_enabled=lambda: self.enabled, logger=self.logger, clock=lambda: self.now,
            monotonic=lambda: self.moment, start_worker=start_worker or (lambda work: work()),
        )
        self.service.add_idle_listener(lambda: self.idle.append(1))

    def settings(self) -> ManagerSettings:
        """Return settings with the temporary DayZ folder and the optional SteamCMD folder."""
        return ManagerSettings(2, str(self.dayz), None, self.steamcmd,
                               None if self.steamcmd is None else self.steamcmd + "\\steamcmd.exe",
                               None, None, MappingProxyType({}))

    def record_at(self, age_seconds: float, outcome=AttemptOutcome.OK) -> BuildCheckRecord:
        """Return a cached record whose attempt finished the given seconds before the start."""
        finished = utc_text(START - timedelta(seconds=age_seconds))
        code = None if outcome is AttemptOutcome.OK else "TIMEOUT"
        return BuildCheckRecord(CheckAttempt(finished, outcome, code), finished,
                                {"public": BranchFact(24570360, 1786528820)})

    def test_start_check_runs_once_and_reads_current(self) -> None:
        """The start trigger runs once; the recorded output gives a current installation."""
        self.assertTrue(self.service.run_scheduled())
        self.assertFalse(self.service.run_scheduled())
        self.assertEqual(self.app.calls, 1)
        self.assertEqual(self.cache.saved[-1].branches["public"], BranchFact(24570360, 1786528820))
        status = self.service.status()
        self.assertEqual((status["state"], status["ownership"], status["installed_build"], status["available_build"]),
                         ("CURRENT", "STEAM_CLIENT", 24570360, 24570360))
        self.assertEqual(self.idle, [1])

    def test_start_debounce_and_interval(self) -> None:
        """A check 5 min before the start counts as the start check; the next one is due 6 h after it."""
        self.build(self.record_at(300))
        self.assertFalse(self.service.run_scheduled())
        self.assertAlmostEqual(self.service.seconds_until_due(), 6 * 3600 - 300)
        self.now = START + timedelta(seconds=6 * 3600 - 300)
        self.assertTrue(self.service.run_scheduled())

    def test_a_failure_does_not_shorten_the_interval(self) -> None:
        """At most every 6 hours, also after a failed attempt."""
        self.app.answer = result(exit_code=7)
        self.assertTrue(self.service.run_scheduled())
        self.assertEqual(self.cache.saved[-1].last_attempt.error_code, "STEAMCMD_FAILED")
        self.now = START + timedelta(hours=5, minutes=59)
        self.assertFalse(self.service.run_scheduled())
        self.now = START + timedelta(hours=6)
        self.assertTrue(self.service.run_scheduled())

    def test_switch_off_stops_automatic_checks_but_not_check_now(self) -> None:
        """D1: the switch governs start and interval; "Check now" ignores it."""
        self.enabled = False
        self.assertFalse(self.service.run_scheduled())
        self.assertEqual(self.app.calls, 0)
        self.assertTrue(self.service.request(True)["accepted"])
        self.assertFalse(self.service.request(False)["accepted"])
        self.assertEqual(self.app.calls, 1)

    def test_forced_debounce(self) -> None:
        """A forced request within 60 s of the last attempt starts nothing."""
        self.assertTrue(self.service.request(True)["accepted"])
        self.now += timedelta(seconds=59)
        self.assertFalse(self.service.request(True)["accepted"])
        self.now += timedelta(seconds=1)
        self.assertTrue(self.service.request(True)["accepted"])

    def test_forced_request_waits_while_busy_and_runs_when_free(self) -> None:
        """Lane busy: waiting; a second request changes nothing; it runs at the next free evaluation."""
        self.drained = False
        answer = self.service.request(True)
        self.assertEqual(answer, {"accepted": False, "checking": False, "waiting": True})
        revision = self.service.check_view()["revision"]
        self.service.request(True)
        self.assertEqual(self.service.check_view()["revision"], revision)
        self.assertEqual(self.service.seconds_until_due(), 5.0)
        self.assertFalse(self.service.run_scheduled())
        self.drained = True
        self.assertTrue(self.service.run_scheduled())
        self.assertFalse(self.service.check_view()["waiting"])
        self.assertEqual(self.app.calls, 1)

    def test_waiting_expires_after_15_minutes(self) -> None:
        """A forced request that never found a free lane is dropped without a record."""
        self.drained = False
        self.service.request(True)
        self.moment += 15 * 60
        self.assertFalse(self.service.run_scheduled())
        self.assertFalse(self.service.check_view()["waiting"])
        self.assertEqual(self.cache.saved, [])

    def test_a_lost_race_in_the_worker_waits_and_does_not_wake_the_scheduler(self) -> None:
        """The pre-test passed, but an operation took the guard before the worker's try."""
        def steal_then_run(work) -> None:
            """Let an operation take the guard between the pre-test and the worker."""
            self.assertTrue(self.guard.try_hold())
            work()
            self.guard.release()

        self.build(start_worker=steal_then_run)
        self.assertTrue(self.service.request(True)["accepted"])
        self.assertTrue(self.service.check_view()["waiting"])
        self.assertEqual((self.app.calls, self.cache.saved, self.idle), (0, [], []))
        self.build(start_worker=lambda work: (setattr(self, "drained", False), work()))
        self.drained = True
        self.assertTrue(self.service.run_scheduled())
        self.assertEqual((self.app.calls, self.cache.saved, self.idle), (0, [], []))
        self.assertTrue(self.guard.available())

    def test_not_configured_records_nothing_and_runs_after_setup(self) -> None:
        """No SteamCMD: no process and no attempt; the due check runs once SteamCMD is set up."""
        self.steamcmd = None
        self.assertFalse(self.service.run_scheduled())
        self.assertFalse(self.service.request(True)["waiting"])
        self.assertEqual((self.app.calls, self.cache.saved), (0, []))
        self.assertEqual(self.service.status()["reason"], "STEAMCMD_NOT_CONFIGURED")
        self.steamcmd = str(self.root / "SteamCMD")
        self.assertTrue(self.service.run_scheduled())

    def test_an_unproven_exit_poisons_the_guard_and_stops_every_later_check(self) -> None:
        """Architect change 1: the holder poisons the guard; no check or forced request runs again."""
        self.app.answer = result(confirmed=False)
        self.assertTrue(self.service.run_scheduled())
        self.assertTrue(self.guard.poisoned)
        status = self.service.status()
        self.assertEqual((status["state"], status["reason"], status["error_code"]),
                         ("COULD_NOT_CHECK", "FAILED", "STEAMCMD_EXIT_UNPROVEN"))
        self.now += timedelta(hours=7)
        self.assertFalse(self.service.run_scheduled())
        self.assertFalse(self.service.request(True)["accepted"])
        self.assertEqual(self.app.calls, 1)
        self.assertIn(("server_build.steamcmd_exit_unproven", "ERROR"), [event[:2] for event in self.logger.events])

    def test_a_shutdown_records_nothing(self) -> None:
        """A run that the closing application cancelled leaves no attempt."""
        self.app.during = lambda _cancel: self.service.cancel()
        self.app.answer = result(cancelled=True, exit_code=None)
        self.assertTrue(self.service.run_scheduled())
        self.assertEqual(self.cache.saved, [])
        self.assertFalse(self.service.run_scheduled())

    def test_internal_errors_and_status_never_raise(self) -> None:
        """An exception in a run records INTERNAL; a status read never fails."""
        def explode(_cancel) -> None:
            """Fail inside the run."""
            raise KeyError("boom")

        self.app.during = explode
        self.assertTrue(self.service.run_scheduled())
        self.assertEqual(self.cache.saved[-1].last_attempt.error_code, "INTERNAL")
        self.assertTrue(self.guard.available())
        self.service._settings = lambda: (_ for _ in ()).throw(OSError("settings"))
        self.assertEqual(self.service.status()["error_code"], "INTERNAL")

    def test_nothing_personal_reaches_the_status_the_cache_or_the_log(self) -> None:
        """LastOwner, paths and the branch name never leave the reader."""
        self.dayz.parent.parent.joinpath("appmanifest_223350.acf").write_text(
            manifest(UserConfig={"betakey": "secret_beta"}, MountedConfig={"betakey": "secret_beta"}),
            encoding="utf-8")
        self.service.run_scheduled()
        text = json.dumps([self.service.status(), [record.branches for record in self.cache.saved],
                           self.logger.events], default=repr)
        for secret in (OWNER_MARKER, str(self.root), "Steam Library"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text.replace("\\\\", "\\"))
        completed = [fields for event, _level, fields in self.logger.events if event == "server_build.check_completed"]
        self.assertEqual(completed[0]["branch_kind"], "OTHER")
        self.assertNotIn("secret_beta", json.dumps(self.logger.events))

    def test_a_poisoned_guard_ends_a_waiting_check_now(self) -> None:
        """QF-050 (QA reproduction): a lane holder poisons the guard while "Check now" waits."""
        self.drained = False
        self.assertTrue(self.service.request(True)["waiting"])
        self.assertTrue(self.guard.try_hold())
        self.guard.release(unproven=True)
        self.drained = True
        revision = self.service.check_view()["revision"]
        self.assertFalse(self.service.run_scheduled())
        view = self.service.check_view()
        self.assertEqual((view["waiting"], view["paused"]), (False, True))
        self.assertGreater(view["revision"], revision)
        self.assertNotEqual(self.service.seconds_until_due(), 5.0)
        for minutes in (16, 60, 24 * 60):
            self.moment = 1000.0 + minutes * 60
            self.assertFalse(self.service.run_scheduled())
            self.assertFalse(self.service.status()["waiting"])
        self.assertEqual(self.service.request(True), {"accepted": False, "checking": False, "waiting": False})
        self.assertTrue(self.service.status()["paused"])
        self.assertEqual(self.app.calls, 0)

    def test_the_worker_tries_the_guard_before_it_reads_the_lane(self) -> None:
        """QF-052, decision 3: the worker's read of is_drained() happens while it holds the guard."""
        seen: list[bool] = []
        self.service._lane_drained = lambda: (seen.append(self.guard.available()), True)[1]
        self.assertTrue(self.service.request(True)["accepted"])
        # The pre-test reads a free guard; the worker reads the lane only with the guard held
        self.assertEqual(seen, [True, False])
        self.assertEqual(self.app.calls, 1)

    def test_stop_ends_a_running_check_without_a_record(self) -> None:
        """Shutdown: the worker sees the cancellation and the service waits for it."""
        started = threading.Event()

        def block(cancellation_requested) -> None:
            """Wait until the run is cancelled."""
            started.set()
            while not cancellation_requested():
                pass

        self.app.during = block
        self.app.answer = result(cancelled=True, exit_code=None)
        self.build(start_worker=lambda work: (thread := threading.Thread(target=work, daemon=True), thread.start(), thread)[2])
        self.assertTrue(self.service.request(True)["accepted"])
        started.wait(2)
        self.assertTrue(self.service.check_view()["checking"])
        self.assertTrue(self.service.stop(2.0))
        self.assertEqual(self.cache.saved, [])
        self.assertTrue(self.guard.available())


if __name__ == "__main__":
    unittest.main()
