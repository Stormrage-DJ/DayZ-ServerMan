"""DayZ server build check: triggers, one run at a time under the SteamCMD guard, and the status.

The check runs off the operation lane on its own worker. It starts a run only
when the SteamCMD guard is free and the lane is drained; otherwise a scheduled
check is skipped and a forced one waits (detailed design 14.5 and 14.6).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ..domain.models import ManagerSettings
from ..domain import server_build as rules
from ..domain.server_build import BranchFact, BuildCheckRecord
from ..domain.update_check import AttemptOutcome, CheckAttempt
from ..domain.update_check_rules import attempt_age_seconds, check_state, utc_text
from ..observability.structured_log import StructuredLogger
from .server_build_run import (
    AppInfoPort,
    BuildCachePort,
    ExecutablePreflightPort,
    RunOutcome,
    run_newest_build_check,
    start_thread,
    steamcmd_configured,
)
from .server_build_events import BuildCheckEvents
from .server_build_status import InstalledMemo, internal_view, server_build_view
from .steamcmd_guard import SteamCmdRunGuard


class ServerBuildService:
    """Own the newest-build facts, decide each trigger and run at most one check at a time."""

    def __init__(
        self, preflight: ExecutablePreflightPort, app_info: AppInfoPort, cache: BuildCachePort,
        settings: Callable[[], ManagerSettings], guard: SteamCmdRunGuard,
        lane_drained: Callable[[], bool], *,
        automatic_enabled: Callable[[], bool] = lambda: True,
        logger: StructuredLogger | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        start_worker: Callable[[Callable[[], None]], Any] = start_thread,
        installed: InstalledMemo | None = None,
    ) -> None:
        """Store the collaborators and start from the cached record."""
        self._preflight, self._app_info, self._cache = preflight, app_info, cache
        self._settings, self._guard, self._lane_drained = settings, guard, lane_drained
        self._automatic_enabled = automatic_enabled
        self._clock, self._monotonic, self._start_worker = clock, monotonic, start_worker
        self._installed = installed or InstalledMemo(monotonic, logger)
        # One lock guards the fields below; it is never held during I/O, a try of the guard or logging
        self._lock = threading.Condition()
        record = cache.load() or BuildCheckRecord()
        self._attempt, self._last_success_at = record.last_attempt, record.last_success_at
        self._branches: dict[str, BranchFact] = dict(record.branches)
        self._running, self._start_done = False, False
        self._events = BuildCheckEvents(logger)
        self._waiting_since: float | None = None
        self._revision = 0
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._idle_listeners: list[Callable[[], None]] = []

    def add_idle_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback that fires after every run has ended."""
        self._idle_listeners.append(listener)

    def check_view(self) -> dict[str, Any]:
        """Return the check state and attempt data without any I/O."""
        now = self._clock()
        with self._lock:
            attempt = self._attempt
            return {
                "check_state": check_state(
                    attempt, self._last_success_at, now, rules.INTERVAL_SECONDS,
                ).value,
                "checked_at": attempt.finished_at if attempt else None,
                "last_success_at": self._last_success_at,
                "error_code": attempt.error_code if attempt else None,
                "checking": self._running, "waiting": self._waiting_since is not None,
                "revision": self._revision, "branches": dict(self._branches),
                # An unproven SteamCMD exit poisoned the guard: checks are paused for the session
                "paused": self._guard.poisoned,
            }

    def status(self) -> dict[str, object]:
        """Return the `server_build` object of the update status; never raises."""
        check: dict[str, Any] | None = None
        try:
            check = self.check_view()
            settings = self._settings()
            return server_build_view(
                self._installed.read(settings), check, check.pop("branches"),
                steamcmd_configured=steamcmd_configured(settings),
            )
        except Exception:
            return internal_view(check)

    def run_scheduled(self) -> bool:
        """Start a run for a pending forced request or a due start or interval trigger."""
        if self._guard.poisoned or self._shutdown.is_set():
            # No run can start again in this session: a waiting "Check now" ends here (QF-050)
            self._end_waiting()
            return False
        enabled = self._enabled()
        now, moment = self._clock(), self._monotonic()
        with self._lock:
            if self._running:
                return False
            # A forced request that waited too long is dropped without a record
            waited = moment - self._waiting_since if self._waiting_since is not None else 0.0
            if waited >= rules.WAITING_EXPIRY_SECONDS:
                self._waiting_since = None
                self._revision += 1
            trigger: str | None = "FORCED"
            if self._waiting_since is None:
                trigger, self._start_done = rules.due_trigger(
                    self._attempt, self._start_done, now, enabled,
                )
        if trigger is None:
            return False
        # A pre-test spares a worker while the lane or the guard is busy; the worker's try decides
        busy = self._busy_reason()
        if busy is not None:
            self._lost(trigger, busy)
            return False
        return self._launch(trigger)

    def seconds_until_due(self) -> float:
        """Return the seconds until the next evaluation is useful; infinite during a run."""
        now = self._clock()
        with self._lock:
            if self._running:
                return float("inf")
            return rules.seconds_until_due(
                self._attempt, self._start_done, self._waiting_since is not None, now,
            )

    def request(self, force: bool) -> dict[str, bool]:
        """Handle "Check now"; a request that is not forced starts no run."""
        accepted = False
        if self._guard.poisoned or self._shutdown.is_set():
            self._end_waiting()
        elif force:
            accepted = self._forced(self._clock())
        with self._lock:
            return {"accepted": accepted, "checking": self._running,
                    "waiting": self._waiting_since is not None}

    def stop(self, timeout: float = 30.0) -> bool:
        """Cancel a running check and wait for its worker; report whether it ended."""
        self._shutdown.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def cancel(self) -> None:
        """Ask a running check to end; called when the application begins to close."""
        self._shutdown.set()

    def _forced(self, now: datetime) -> bool:
        """Start a forced run, or remember it as waiting when the lane or the guard is busy."""
        with self._lock:
            age = attempt_age_seconds(self._attempt, now)
            if self._running or (age is not None and age < rules.FORCED_DEBOUNCE_SECONDS):
                return False
        # A pre-test only: the worker's try of the guard decides
        if self._busy_reason() is None:
            return self._launch("FORCED")
        if self._configured():
            self._wait()
        return False

    def _launch(self, trigger: str) -> bool:
        """Claim the single run slot and hand the run to its worker."""
        if not self._configured():
            self._events.skipped("NOT_CONFIGURED")
            with self._lock:
                self._waiting_since = None
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._revision += 1
        try:
            thread = self._start_worker(lambda: self._run(trigger))
        except Exception:
            self._end_run()
            return False
        with self._lock:
            self._thread = thread if isinstance(thread, threading.Thread) else self._thread
        return True

    def _busy_reason(self) -> str | None:
        """Return why a run cannot start now, or None; called without the lock held."""
        if not self._guard.available():
            return "STEAMCMD_BUSY"
        return None if self._lane_drained() else "LANE_BUSY"

    def _run(self, trigger: str) -> None:
        """Run on the worker: try the guard, then the drained lane, then the command."""
        ran = False
        try:
            if not self._guard.try_hold():
                return self._lost(trigger, "STEAMCMD_BUSY")
            outcome: RunOutcome | None = None
            try:
                if not self._lane_drained():
                    return self._lost(trigger, "LANE_BUSY")
                ran = True
                with self._lock:
                    self._waiting_since = None
                    self._start_done = self._start_done or trigger == "START"
                self._events.started(trigger)
                settings = self._settings()
                outcome = run_newest_build_check(
                    settings, self._preflight, self._app_info, self._shutdown, self._monotonic,
                )
            finally:
                # The holder that saw an unproven exit poisons the guard before it releases it
                self._guard.release(unproven=outcome is not None and outcome.unproven)
            self._record(outcome, settings)
        except Exception:
            ran = True
            self._record(RunOutcome(error_code="INTERNAL"), None)
        finally:
            # A lost race does not wake the scheduler, so a busy lane costs no busy loop
            self._end_run(notify=ran)

    def _record(self, outcome: RunOutcome, settings: ManagerSettings | None) -> None:
        """Merge one outcome, write the cache and log it; shutdown and no setup record nothing."""
        if outcome.not_configured or outcome.shutdown:
            return
        finished = utc_text(self._clock())
        with self._lock:
            if outcome.error_code is None:
                self._attempt = CheckAttempt(finished, AttemptOutcome.OK, None)
                self._last_success_at, self._branches = finished, dict(outcome.branches)
            else:
                self._attempt = CheckAttempt(finished, AttemptOutcome.FAILED, outcome.error_code)
            self._revision += 1
            record = BuildCheckRecord(self._attempt, self._last_success_at, dict(self._branches))
        self._cache.save(record)
        self._events.completed(outcome, self.status() if settings is not None else internal_view())

    def _lost(self, trigger: str, reason: str) -> None:
        """A run that could not start: a forced one waits, a scheduled one is skipped."""
        if trigger == "FORCED":
            self._wait()
        self._events.skipped(reason)

    def _end_waiting(self) -> None:
        """Drop a waiting forced request without a record."""
        with self._lock:
            if self._waiting_since is not None:
                self._waiting_since = None
                self._revision += 1

    def _wait(self) -> None:
        """Remember one forced request until the lane and the guard are free."""
        with self._lock:
            if self._waiting_since is None:
                self._waiting_since = self._monotonic()
                self._revision += 1

    def _end_run(self, *, notify: bool = True) -> None:
        """Clear the running flag, wake waiters and tell the idle listeners after a real run."""
        with self._lock:
            self._running = False
            self._lock.notify_all()
        for listener in tuple(self._idle_listeners) if notify else ():
            try:
                listener()
            except Exception:
                continue

    def _configured(self) -> bool:
        """Return whether SteamCMD is set up; unreadable settings count as not set up."""
        try:
            return steamcmd_configured(self._settings())
        except Exception:
            return False

    def _enabled(self) -> bool:
        """Return the automatic-check switch; an unreadable switch counts as off."""
        try:
            return self._automatic_enabled() is True
        except Exception:
            return False
