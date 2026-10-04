"""Single-flight remote update check that never runs on a bridge caller or the lane."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ..domain.update_check import (
    AttemptOutcome,
    CheckAttempt,
    RemoteBatchFailure,
    RemoteFact,
    UpdateCheckRecord,
)
from ..domain import update_check_rules as rules
from ..observability.structured_log import StructuredLogger
from .update_check_ports import WorkshopRemoteCatalogPort
from .update_check_run import fetch_facts, select_ids


class UpdateCheckCachePort(Protocol):
    """Disposable persistence of the remote facts; neither call raises."""

    # Return the stored record, or None when nothing usable is stored
    def load(self) -> UpdateCheckRecord | None: ...

    # Write the record for the configured ids; False reports a failed write
    def save(self, record: UpdateCheckRecord, configured_ids: Collection[str]) -> bool: ...


@dataclass(frozen=True)
class CheckSnapshot:
    """Consistent view of the check: derived state, attempt data and facts."""

    check_state: rules.CheckState
    checked_at: str | None
    last_success_at: str | None
    error_code: str | None
    checking: bool
    revision: int
    facts: Mapping[str, RemoteFact]


def _utc_now() -> datetime:
    """Return the current time as an aware UTC value."""
    return datetime.now(UTC)


def _start_thread(work: Callable[[], None]) -> None:
    """Run one check on its own daemon thread."""
    threading.Thread(target=work, name="dayz-serverman-update-check", daemon=True).start()


class UpdateCheckService:
    """Own the remote facts, decide each trigger and run at most one check at a time."""

    def __init__(
        self, catalog: WorkshopRemoteCatalogPort, cache: UpdateCheckCachePort,
        configured_ids: Callable[[], Iterable[str]], *,
        automatic_enabled: Callable[[], bool] = lambda: True,
        logger: StructuredLogger | None = None,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        start_worker: Callable[[Callable[[], None]], None] = _start_thread,
        interval_seconds: float = rules.DEFAULT_INTERVAL_SECONDS,
        wait_seconds: float = rules.RUN_DEADLINE_SECONDS,
    ) -> None:
        """Store the collaborators and start from the cached facts."""
        self._interval = rules.validate_interval(interval_seconds)
        self._catalog, self._cache, self._configured_ids = catalog, cache, configured_ids
        self._automatic_enabled, self._logger = automatic_enabled, logger
        self._clock, self._monotonic, self._start_worker = clock, monotonic, start_worker
        self._wait_seconds = wait_seconds
        # One lock guards the fields below; it is never held during I/O or logging
        self._lock = threading.Condition()
        record = cache.load() or UpdateCheckRecord()
        self._items: dict[str, RemoteFact] = dict(record.items)
        self._attempt = record.last_attempt
        self._last_success_at = record.last_success_at
        self._running = False
        self._failures = 0
        self._revision = 0
        # Count of synchronous calls so far, and per id the count when its merged answer arrived
        self._call_sequence = 0
        self._answered_at_call: dict[str, int] = {}
        self._idle_listeners: list[Callable[[], None]] = []

    def add_idle_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback that fires after every run has ended."""
        self._idle_listeners.append(listener)

    def snapshot(self) -> CheckSnapshot:
        """Return the current check state and facts without any I/O."""
        now = self._clock()
        with self._lock:
            attempt = self._attempt
            return CheckSnapshot(
                rules.check_state(attempt, self._last_success_at, now, self._interval),
                attempt.finished_at if attempt else None, self._last_success_at,
                attempt.error_code if attempt else None,
                self._running, self._revision, dict(self._items),
            )

    def run_scheduled(self) -> bool:
        """Start a run for the "start, interval" trigger; return whether one started."""
        enabled = self._enabled()
        return self._start_if(lambda now: rules.scheduled_run_allowed(
            enabled=enabled, checking=False, attempt=self._attempt,
            failures=self._failures, now=now, interval_seconds=self._interval,
        ))

    def seconds_until_due(self) -> float:
        """Return the seconds until the next scheduled check; infinite during a run."""
        now = self._clock()
        with self._lock:
            if self._running:
                return float("inf")
            due = rules.next_due(self._attempt, self._failures, now, self._interval)
        return max(0.0, (due - now).total_seconds())

    def request(self, force: bool) -> tuple[bool, bool]:
        """Handle a shell request; return (a run was started, a run is active now)."""
        if force:
            # "Check now" ignores the switch and backoff but is debounced
            started = self._start_if(lambda now: rules.forced_run_allowed(
                checking=False, attempt=self._attempt, now=now,
            ))
        else:
            enabled = self._enabled()

            def allowed(fact_missing: bool) -> Callable[[datetime], bool]:
                """Return the non-forced rule for a known fact gap."""
                return lambda now: rules.requested_run_allowed(
                    enabled=enabled, checking=False, attempt=self._attempt,
                    failures=self._failures, now=now, interval_seconds=self._interval,
                    fact_missing=fact_missing,
                )

            # Read the profiles only when the age rule alone refused the run
            started = self._start_if(allowed(False))
            if not started and enabled and self._fact_missing():
                started = self._start_if(allowed(True))
        with self._lock:
            return started, self._running

    def check_now(self, ids: Iterable[str]) -> dict[str, RemoteFact]:
        """Join or start a run and return only the facts answered during this call.

        No clock decides: a fact counts only when a run merged it and its batch
        answered after this call was counted. Anything else gives no entry.
        """
        wanted = tuple(ids)
        # Count this call before the run can answer, so earlier answers stay excluded
        with self._lock:
            self._call_sequence += 1
            call = self._call_sequence
        self._start_if(lambda _now: True, wanted)
        with self._lock:
            # Wait for the run to end; a timeout yields no fact at all
            if not self._lock.wait_for(lambda: not self._running, self._wait_seconds):
                return {}
            # Keep the facts whose merged answer arrived after this call began
            return {
                key: self._items[key] for key in wanted
                if key in self._items and self._answered_at_call.get(key, 0) >= call
            }

    def _start_if(
        self, allowed: Callable[[datetime], bool], extra: tuple[str, ...] = (),
    ) -> bool:
        """Start one run when none is active and the rule allows it."""
        now = self._clock()
        # Claim the single run slot and publish the change
        with self._lock:
            if self._running or not allowed(now):
                return False
            self._running = True
            self._revision += 1
        # Hand the run to its own worker context
        try:
            self._start_worker(lambda: self._run(extra))
        except Exception:
            # A worker that cannot start is a failed attempt, never a stuck flag
            self._merge({}, RemoteBatchFailure.INTERNAL, None)
            self._end_run()
            return False
        return True

    def _run(self, extra: tuple[str, ...]) -> None:
        """Execute one run: fetch unlocked, merge locked, write the cache unlocked."""
        try:
            configured: frozenset[str] | None = None
            # Per answered id of this run: the count of synchronous calls at its arrival
            answered: dict[str, int] = {}
            try:
                # Collect the ids of all readable profiles plus the caller's ids
                configured = frozenset(self._configured_ids())
                ids, dropped = select_ids(configured, extra)
                if dropped:
                    self._log("update_check.ids_capped", {"dropped": dropped}, "WARNING")
                facts, failure = fetch_facts(
                    self._catalog, ids, clock=self._clock, monotonic=self._monotonic,
                    deadline_seconds=rules.RUN_DEADLINE_SECONDS,
                    on_answer=lambda batch: answered.update(dict.fromkeys(batch, self._calls())),
                )
            except Exception:
                # Any error inside the run is recorded as an internal failure
                facts, failure = {}, RemoteBatchFailure.INTERNAL
            record = self._merge(facts, failure, configured, answered)
            # Persist for the configured ids; keep every fact when they are unknown
            self._cache.save(
                record, configured if configured is not None else frozenset(record.items),
            )
            self._log("update_check.completed", {
                "outcome": record.last_attempt.outcome.value,
                "code": record.last_attempt.error_code, "fact_count": len(facts),
            }, "INFO" if failure is None else "WARNING")
        except Exception:
            self._log("update_check.run_failed", {}, "ERROR")
        finally:
            self._end_run()

    def _merge(
        self, facts: Mapping[str, RemoteFact], failure: RemoteBatchFailure | None,
        configured: frozenset[str] | None, answered: Mapping[str, int] | None = None,
    ) -> UpdateCheckRecord:
        """Store the run result under the lock and return the record to persist.

        `answered` gives each fact of this run its mark; only a merged fact gets one.
        """
        finished = rules.utc_text(self._clock())
        marks = answered or {}
        with self._lock:
            self._items.update(facts)
            self._answered_at_call.update({key: marks.get(key, 0) for key in facts})
            # Forget facts of ids that no readable profile configures any more
            if configured is not None:
                keep = configured | set(facts)
                self._items = {key: fact for key, fact in self._items.items() if key in keep}
            # A mark never outlives its fact
            self._answered_at_call = {
                key: mark for key, mark in self._answered_at_call.items() if key in self._items
            }
            # Keep outcome and error code consistent: OK has no code, FAILED has one
            if failure is None:
                self._attempt = CheckAttempt(finished, AttemptOutcome.OK, None)
                self._last_success_at = finished
                self._failures = 0
            else:
                self._attempt = CheckAttempt(finished, AttemptOutcome.FAILED, failure.value)
                self._failures += 1
            self._revision += 1
            return UpdateCheckRecord(self._attempt, self._last_success_at, dict(self._items))

    def _end_run(self) -> None:
        """Clear the running flag, wake waiters and tell the idle listeners."""
        with self._lock:
            self._running = False
            self._lock.notify_all()
        for listener in tuple(self._idle_listeners):
            try:
                listener()
            except Exception:
                continue

    def _calls(self) -> int:
        """Return how many synchronous calls have begun so far."""
        with self._lock:
            return self._call_sequence

    def _enabled(self) -> bool:
        """Return the automatic-check switch; an unreadable switch counts as off."""
        try:
            return self._automatic_enabled() is True
        except Exception:
            return False

    def _fact_missing(self) -> bool:
        """Return whether a configured id within the run limit has no fact yet."""
        try:
            ids, _dropped = select_ids(self._configured_ids())
        except Exception:
            return False
        with self._lock:
            return any(key not in self._items for key in ids)

    def _log(self, event: str, fields: Mapping[str, Any], level: str) -> None:
        """Emit one structured event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(event, level=level, fields=fields)
        except (OSError, TypeError, ValueError):
            return
