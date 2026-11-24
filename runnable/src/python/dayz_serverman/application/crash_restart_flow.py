"""Crash restart flow: find a READY launch that ended without a manager stop and restart it once."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ..domain.lifecycle import LaunchEvidence, LifecycleSnapshot, ServerState
from ..observability.structured_log import StructuredLogger
from .crash_restart_policy import (
    CRASH_RESTART_WAIT_SECONDS,
    CrashAction,
    CrashReason,
    decide_crash,
    guard_refusal,
    is_crash,
)
from .lifecycle_ownership import LaunchOwnership
from .operations.models import OperationState


@dataclass(frozen=True)
class CrashRestartState:
    """What the flow shows for the last crash: pending, restarted or gave_up."""

    # "pending", "restarted" or "gave_up"
    kind: str
    # CrashReason value for "gave_up", else None
    reason: str | None
    # UTC ISO text with milliseconds, from the wall clock
    at: str


class CrashWatchLifecycle(Protocol):
    """The part of the lifecycle service that the flow reads."""

    def status(self) -> LifecycleSnapshot: ...

    def release_handle(self, handle_token: str | None) -> None: ...


class CrashRestartLanePort(Protocol):
    """The part of the operation lane that the flow uses."""

    def lifecycle_operation_ids(self) -> frozenset[str]: ...

    def submit(self, profile_id: str, still_wanted: Callable[[], bool]) -> str: ...

    def state(self, operation_id: str) -> OperationState: ...


@dataclass(eq=False)
class _Pending:
    """A crash that waits for its restart."""

    profile_id: str
    dead_token: str
    detected_at: float
    known_operations: frozenset[str]
    operation_id: str | None = None


class CrashRestartFlow:
    """Poll the status, claim a crash once and restart the server after a short wait."""

    def __init__(self, lifecycle: CrashWatchLifecycle, ownership: LaunchOwnership, lane: CrashRestartLanePort,
                 logger: StructuredLogger, *, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Bind the collaborators and start without a watched launch."""
        self._lifecycle = lifecycle
        self._ownership = ownership
        self._lane = lane
        self._logger = logger
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        # Token and profile id of the launch seen running; None when the profile is not known
        self._watch: tuple[str, str] | None = None
        self._pending: _Pending | None = None
        # Token and profile id of the restarted launch that must still become READY
        self._follow: tuple[str, str] | None = None
        self._state: CrashRestartState | None = None
        # Monotonic times of the crash restarts that the loop guard counts
        self._restart_times: list[float] = []

    def tick(self) -> None:
        """Read the status once and apply the running or the end rules; a failure is logged."""
        try:
            status = self._lifecycle.status()
            phase, evidence = self._ownership.snapshot()
            now = self._clock()
            with self._lock:
                if status.state == ServerState.RUNNING_MANAGED and evidence is not None:
                    self._running(evidence, status)
                elif status.state == ServerState.STOPPED and phase is None and evidence is not None:
                    self._ended(evidence, now)
        except Exception as error:
            self._emit("server_crash.tick_failed", "ERROR", {"error": type(error).__name__})

    def read(self) -> CrashRestartState | None:
        """Return the last crash state under the lock."""
        with self._lock:
            return self._state

    def cancel(self) -> bool:
        """Drop a pending restart; True when one was pending."""
        with self._lock:
            if self._pending is None:
                return False
            self._pending = None
            self._state = None
            return True

    def clear(self) -> bool:
        """Drop a pending restart and the stored state of any kind; True when something was dropped."""
        with self._lock:
            cleared = self._pending is not None or self._state is not None
            self._pending = None
            self._state = None
            return cleared

    def _running(self, evidence: LaunchEvidence, status: LifecycleSnapshot) -> None:
        """Remember the running launch and its profile; stop following a launch that became READY."""
        token = evidence.launch_token
        self._watch = (token, status.profile_id) if status.profile_id is not None else None
        pending = self._pending
        if pending is not None:
            if pending.operation_id is not None and token != pending.dead_token:
                # The restart worked: follow the new launch until it is READY
                self._follow = (token, pending.profile_id)
                self._pending = None
                self._state = CrashRestartState("restarted", None, self._wall_text())
                self._emit("server_crash.restarted", "INFO", {
                    "profile_id": pending.profile_id, "operation_id": pending.operation_id,
                    "trigger": "crash_restart",
                })
            else:
                # Someone else started the server
                self._pending = None
                self._state = None
        if self._follow is not None and self._follow[0] == token and self._ownership.launch_facts(token).ready:
            self._follow = None

    def _ended(self, evidence: LaunchEvidence, now: float) -> None:
        """Apply the end rules to a launch that the status shows as stopped."""
        token = evidence.launch_token
        facts = self._ownership.launch_facts(token)
        pending = self._pending
        if pending is not None and pending.operation_id is not None:
            outcome = self._lane.state(pending.operation_id)
            if outcome in (OperationState.FAILED, OperationState.RECOVERY_REQUIRED, OperationState.CANCELLED):
                self._give_up(CrashReason.START_FAILED, pending.profile_id)
                return
            if outcome != OperationState.SUCCEEDED:
                return
            self._pending = None
            if token == pending.dead_token:
                # The start did nothing
                self._state = None
                return
            # The new launch ended before a poll saw it running
            self._follow = (token, pending.profile_id)
        # A restarted launch that ended before READY is not restarted again
        if self._follow is not None and self._follow[0] == token:
            profile_id = self._follow[1]
            self._follow = None
            if not facts.ready:
                self._give_up(CrashReason.ENDED_BEFORE_READY, profile_id)
                return
        if self._pending is not None:
            self._wait_or_restart(self._pending, now)
            return
        # A launch that was never seen running has no profile; it is not claimed
        if self._watch is None or self._watch[0] != token:
            return
        if not is_crash(facts, True) or not self._ownership.claim_end(token):
            return
        if evidence.handle_token is not None:
            try:
                self._lifecycle.release_handle(evidence.handle_token)
            except OSError:
                pass
        profile_id = self._watch[1]
        reason = guard_refusal(self._restart_times, now)
        if reason is not None:
            self._give_up(reason, profile_id)
            return
        self._pending = _Pending(profile_id, token, now, self._lane.lifecycle_operation_ids())
        self._state = CrashRestartState("pending", None, self._wall_text())
        self._emit("server_crash.restart_pending", "INFO",
                   {"profile_id": profile_id, "wait_seconds": CRASH_RESTART_WAIT_SECONDS})

    def _wait_or_restart(self, pending: _Pending, now: float) -> None:
        """Wait out the delay, then submit the restart once; the guard is checked first."""
        decision = decide_crash(self._restart_times, now, pending.detected_at)
        if decision.action == CrashAction.GIVE_UP and decision.reason is not None:
            self._give_up(decision.reason, pending.profile_id)
        elif decision.action == CrashAction.RESTART:
            self._restart(pending, now)

    def _restart(self, pending: _Pending, now: float) -> None:
        """Submit the crash start unless an operator acted after the crash."""
        if self._lane.lifecycle_operation_ids() - pending.known_operations:
            self._pending = None
            self._state = None
            return
        try:
            operation_id = self._lane.submit(pending.profile_id, lambda: self._still_wanted(pending))
        except Exception:
            self._give_up(CrashReason.START_FAILED, pending.profile_id)
            return
        pending.operation_id = operation_id
        self._restart_times.append(now)

    def _still_wanted(self, pending: _Pending) -> bool:
        """Check inside the lane that this restart is still wanted; the lock waits for the stored id."""
        with self._lock:
            return (
                self._pending is pending
                and self._lifecycle.status().state == ServerState.STOPPED
                and not self._lane.lifecycle_operation_ids() - pending.known_operations - {pending.operation_id}
            )

    def _give_up(self, reason: CrashReason, profile_id: str) -> None:
        """Drop any pending restart and show why the manager does not restart."""
        self._pending = None
        self._state = CrashRestartState("gave_up", reason.value, self._wall_text())
        self._emit("server_crash.gave_up", "WARNING", {"profile_id": profile_id, "reason": reason.value})

    def _wall_text(self) -> str:
        """Return the wall time as UTC ISO text with milliseconds."""
        return self._wall_clock().astimezone(UTC).isoformat(timespec="milliseconds")

    def _emit(self, event: str, level: str, fields: dict[str, Any]) -> None:
        """Write one log record; a logging failure never reaches the flow."""
        try:
            self._logger.emit(event, level=level, fields=fields)
        except (OSError, TypeError, ValueError):
            pass
