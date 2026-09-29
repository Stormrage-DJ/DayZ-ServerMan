"""Application drain and safe-close state machine."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..observability.structured_log import StructuredLogger
from .operations.manager import OperationManager


class ShutdownState(str, Enum):
    """Drain phases from accepting work to fully closed."""

    OPEN = "OPEN"
    DRAINING = "DRAINING"
    WAITING_FOR_SAFE_POINT = "WAITING_FOR_SAFE_POINT"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class ShutdownSnapshot:
    """Immutable view of the shutdown state and its blocking reason."""

    state: ShutdownState
    blocking_reason: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Serialize the snapshot for bridge transport."""
        return {"state": self.state.value, "blocking_reason": self.blocking_reason}


class ShutdownCoordinator:
    """Coordinate application drain to a safe close."""

    def __init__(
        self,
        operations: OperationManager,
        logger: StructuredLogger,
        close_guard: Callable[[], bool] | None = None,
    ) -> None:
        """Store collaborators and initialize the OPEN, unblocked state."""
        self._operations = operations
        self._logger = logger
        self._state = ShutdownState.OPEN
        self._blocking_reason: str | None = None
        self._close_guard = close_guard or (lambda: True)
        self._lock = threading.Lock()

    def snapshot(self) -> ShutdownSnapshot:
        """Return the current snapshot, refreshing while a drain is pending."""
        with self._lock:
            snapshot = ShutdownSnapshot(self._state, self._blocking_reason)
        # Pending drain states must reflect live operation state
        if snapshot.state in {ShutdownState.DRAINING, ShutdownState.WAITING_FOR_SAFE_POINT}:
            return self.refresh()
        return snapshot

    def request_shutdown(self) -> ShutdownSnapshot:
        """Begin draining and return the resulting snapshot."""
        with self._lock:
            # Repeated requests after close are no-ops
            if self._state == ShutdownState.CLOSED:
                return ShutdownSnapshot(self._state, self._blocking_reason)
            if self._state == ShutdownState.OPEN:
                self._state = ShutdownState.DRAINING
                self._log("shutdown.requested")
        # Stop accepting new operations, then report drain progress
        self._operations.begin_shutdown()
        return self.refresh()

    def refresh(self) -> ShutdownSnapshot:
        """Recompute the drain state from operation and close-guard state."""
        with self._lock:
            # OPEN and CLOSED are stable; only active drain states recompute
            if self._state == ShutdownState.OPEN:
                return ShutdownSnapshot(self._state, self._blocking_reason)
            if self._state == ShutdownState.CLOSED:
                return ShutdownSnapshot(self._state, self._blocking_reason)
            # Wait for in-flight operations to reach their safe points
            if not self._operations.is_drained():
                self._state = ShutdownState.WAITING_FOR_SAFE_POINT
                self._blocking_reason = "ACTIVE_OPERATION"
            # A held managed-server guard also blocks the close
            elif not self._close_guard():
                self._state = ShutdownState.WAITING_FOR_SAFE_POINT
                self._blocking_reason = "MANAGED_SERVER_ACTIVE"
            else:
                # Close the coordinator and flush buffered log lines
                self._state = ShutdownState.CLOSED
                self._blocking_reason = None
                self._log("shutdown.closed")
                try:
                    self._logger.flush()
                except OSError:
                    pass
            return ShutdownSnapshot(self._state, self._blocking_reason)

    def wait_for_close(self, timeout: float | None = None) -> ShutdownSnapshot:
        """Wait for operation drain, then refresh and return the snapshot."""
        self._operations.wait_for_drain(timeout)
        return self.refresh()

    def _log(self, event: str) -> None:
        """Emit a structured event, ignoring logging failures during shutdown."""
        try:
            self._logger.emit(event)
        except (OSError, TypeError, ValueError):
            return
