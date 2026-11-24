"""Pure rule that decides whether an ended launch is a crash and whether to restart it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .lifecycle_ownership import LaunchFacts

# Crash restarts allowed inside one window
CRASH_RESTART_LIMIT = 3
# Length of the window in seconds: 10 minutes
CRASH_RESTART_WINDOW_SECONDS = 600.0
# Seconds to wait after the end is seen, before the restart
CRASH_RESTART_WAIT_SECONDS = 10.0


class CrashAction(Enum):
    """What to do with a crash."""

    RESTART = "restart"
    WAIT = "wait"
    GIVE_UP = "give_up"


class CrashReason(Enum):
    """Why the manager gives up."""

    GUARD_REACHED = "guard_reached"
    ENDED_BEFORE_READY = "ended_before_ready"
    START_FAILED = "start_failed"


@dataclass(frozen=True)
class CrashDecision:
    """The action for a crash, with the reason when the manager gives up."""

    action: CrashAction
    reason: CrashReason | None


def is_crash(facts: LaunchFacts, stopped_with_evidence: bool) -> bool:
    """Return True for a READY launch that ended without a manager stop and without an earlier claim."""
    return stopped_with_evidence and facts.ready and not facts.stop_intent and not facts.end_claimed


def guard_refusal(restart_times: Sequence[float], now: float) -> CrashReason | None:
    """Return GUARD_REACHED when enough restarts fall in the half-open window (now - 600, now]."""
    recent = [time for time in restart_times if now - CRASH_RESTART_WINDOW_SECONDS < time <= now]
    return CrashReason.GUARD_REACHED if len(recent) >= CRASH_RESTART_LIMIT else None


def decide_crash(restart_times: Sequence[float], now: float, detected_at: float) -> CrashDecision:
    """Give up when the guard refuses, wait during the first 10 seconds, else restart."""
    reason = guard_refusal(restart_times, now)
    if reason is not None:
        return CrashDecision(CrashAction.GIVE_UP, reason)
    if now - detected_at < CRASH_RESTART_WAIT_SECONDS:
        return CrashDecision(CrashAction.WAIT, None)
    return CrashDecision(CrashAction.RESTART, None)
