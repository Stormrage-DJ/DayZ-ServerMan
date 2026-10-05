"""The one in-process SteamCMD run guard (detailed design 14.5 and 14.13).

Lock order: lane, then installation mutex, then this guard. Only lane holders
wait for it, bounded and cancellable; the build check only tries. Each holder
acquires and releases on its own thread. A run whose SteamCMD exit was not
proven poisons the guard for the session: no SteamCMD run starts again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from ..domain.server_build import CHECK_HOLD_SECONDS
from ..observability.structured_log import StructuredLogger
from .operations.context import OperationContext
from .operations.models import OperationFailure

# Longest wait of a lane operation for the guard; it must exceed the longest check hold
LANE_WAIT_SECONDS = 120.0
# Pause between two tries of a waiting lane operation
POLL_SECONDS = 0.5
# Phase of a lane operation that waits for the guard; a declared safe point of both kinds
WAIT_PHASE = "wait_steamcmd"
BUSY_TEXT = "SteamCMD is still busy with the server build check. Try again in a minute."
UNPROVEN_TEXT = ("SteamCMD did not close after an earlier run. Close SteamCMD, "
                 "then restart DayZ-ServerMan.")

assert LANE_WAIT_SECONDS > CHECK_HOLD_SECONDS, "a lane wait must outlast the longest check"


class SteamCmdRunGuard:
    """Exclude two SteamCMD runs of this manager from running at the same time."""

    def __init__(
        self, logger: StructuredLogger | None = None, *,
        wait_seconds: float = LANE_WAIT_SECONDS, poll_seconds: float = POLL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Store the bounds and the clock; the guard starts free and usable."""
        self._lock = threading.Lock()
        self._poisoned = False
        self._logger = logger
        self._wait_seconds, self._poll_seconds, self._monotonic = wait_seconds, poll_seconds, monotonic

    @property
    def poisoned(self) -> bool:
        """Return whether an unproven SteamCMD exit made the guard unusable for the session."""
        return self._poisoned

    def available(self) -> bool:
        """Return whether a try would succeed now; a pre-test only, the try decides."""
        return not self._poisoned and not self._lock.locked()

    def try_hold(self) -> bool:
        """Take the guard without waiting; False when it is held or poisoned."""
        if self._poisoned or not self._lock.acquire(blocking=False):
            return False
        if self._poisoned:
            self._lock.release()
            return False
        return True

    def release(self, *, unproven: bool = False) -> None:
        """Give the guard back; an unproven exit poisons it first."""
        if unproven:
            self._poisoned = True
        self._lock.release()

    def hold_for(self, context: OperationContext, kind: str, percent: int) -> LaneHold:
        """Take the guard for a lane operation, waiting at the safe point `wait_steamcmd`.

        The checkpoint is published once; later tries read the cancellation without
        publishing and call the checkpoint again only to stop there (Architect change 2).
        """
        self._require_usable()
        if self.try_hold():
            return LaneHold(self)
        context.checkpoint("wait_steamcmd", percent)
        started = self._monotonic()
        while True:
            self._require_usable()
            if self._lock.acquire(timeout=self._poll_seconds):
                if self._poisoned:
                    self._lock.release()
                    self._require_usable()
                self._log_wait(kind, started, "ACQUIRED")
                return LaneHold(self)
            if context.cancellation_requested:
                self._log_wait(kind, started, "CANCELLED")
                context.checkpoint("wait_steamcmd", percent)
            if self._monotonic() - started >= self._wait_seconds:
                self._log_wait(kind, started, "TIMEOUT")
                raise OperationFailure("STEAMCMD_BUSY", BUSY_TEXT)

    def _require_usable(self) -> None:
        """Fail a lane operation before its preflight when the guard is poisoned."""
        if self._poisoned:
            raise OperationFailure("STEAMCMD_EXIT_UNPROVEN", UNPROVEN_TEXT)

    def _log_wait(self, kind: str, started: float, outcome: str) -> None:
        """Log how long a lane operation waited for the guard."""
        self._log("steamcmd.guard_waited", {
            "kind": kind, "waited_ms": int((self._monotonic() - started) * 1000), "outcome": outcome,
        })

    def _log(self, event: str, fields: Mapping[str, Any]) -> None:
        """Emit one structured event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(event, fields=fields)
        except (OSError, TypeError, ValueError):
            return


class LaneHold:
    """The guard held by one lane operation; released once, by `release` or at the block end."""

    def __init__(self, guard: SteamCmdRunGuard | None) -> None:
        """Remember the held guard; None is a hold that guards nothing (tests, older wiring)."""
        self._guard = guard

    def release(self, *, unproven: bool = False) -> None:
        """Release the guard once; an unproven SteamCMD exit poisons it."""
        guard, self._guard = self._guard, None
        if guard is not None:
            guard.release(unproven=unproven)

    def __enter__(self) -> LaneHold:
        """Return the hold itself."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the guard when the block ends in any way."""
        self.release()


def hold_steamcmd(
    guard: SteamCmdRunGuard | None, context: OperationContext, kind: str, percent: int,
) -> LaneHold:
    """Take the guard for a lane operation, or return an empty hold without a guard."""
    return guard.hold_for(context, kind, percent) if guard is not None else LaneHold(None)
