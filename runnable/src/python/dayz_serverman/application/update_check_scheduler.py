"""Daemon timer that starts a background check at start and on its interval.

One instance drives the mod check, a second one the server build check.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ..observability.structured_log import StructuredLogger

# Longest wait between two evaluations, in seconds
MAX_WAIT_SECONDS = 60.0


class ScheduledCheck(Protocol):
    """A check that the timer can drive."""

    # Start a run when a trigger is due; return whether one started
    def run_scheduled(self) -> bool: ...

    # Return the seconds until the next useful evaluation
    def seconds_until_due(self) -> float: ...

    # Register a callback that fires after a run has ended
    def add_idle_listener(self, listener: Callable[[], None]) -> None: ...


class UpdateCheckScheduler:
    """Evaluate the "start, interval" trigger; never touches the lane or a bridge call."""

    def __init__(
        self, service: ScheduledCheck, logger: StructuredLogger | None = None,
        *, max_wait_seconds: float = MAX_WAIT_SECONDS, event_prefix: str = "update_check",
    ) -> None:
        """Store the check service and prepare the stop and wake signals.

        `event_prefix` names the check in the scheduler events.
        """
        self._service = service
        self._prefix = event_prefix
        self._logger = logger
        self._max_wait_seconds = max_wait_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        # Evaluate again as soon as a run has ended
        service.add_idle_listener(self.wake)

    def start(self) -> None:
        """Start the daemon thread once, if it is not running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(
                target=self._loop, name=f"dayz-serverman-{self._prefix}-scheduler", daemon=True,
            )
            self._thread.start()
        self._log(f"{self._prefix}.scheduler_started")

    def stop(self, timeout: float = 2.0) -> bool:
        """Signal the thread and report whether it exited within the timeout."""
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        # Signal the loop and wake it from its wait
        self._stop.set()
        self._wake.set()
        thread.join(timeout)
        stopped = not thread.is_alive()
        if stopped:
            with self._lock:
                self._thread = None
            self._log(f"{self._prefix}.scheduler_stopped")
        return stopped

    def wake(self) -> None:
        """Ask for an immediate evaluation, for example after a preference save."""
        self._wake.set()

    def _loop(self) -> None:
        """Evaluate the trigger, then wait for the next due time or a wake signal."""
        # The first evaluation happens at once
        while not self._stop.is_set():
            self._wake.clear()
            try:
                # The service reads the preference and refuses while a run is active
                self._service.run_scheduled()
                due = self._service.seconds_until_due()
            except Exception as error:
                self._log(f"{self._prefix}.scheduler_failed", level="ERROR", fields={
                    "error_type": type(error).__name__,
                })
                due = self._max_wait_seconds
            # A due check that did not start waits for a wake signal or the cap
            wait = min(due, self._max_wait_seconds) if due > 0 else self._max_wait_seconds
            self._wake.wait(wait)

    def _log(
        self, event: str, *, level: str = "INFO", fields: Mapping[str, Any] | None = None,
    ) -> None:
        """Emit one structured event and swallow logging failures."""
        if self._logger is None:
            return
        try:
            self._logger.emit(event, level=level, fields=fields)
        except (OSError, TypeError, ValueError):
            return
