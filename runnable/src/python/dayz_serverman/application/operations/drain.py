"""Operation-lane drain and cancellation policy."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from .models import OperationState, PendingOperation


# Callback signature for publishing an operation state transition
Transition = Callable[[PendingOperation, OperationState, str, int], None]


def cancel_for_shutdown(
    queue: deque[PendingOperation],
    active: PendingOperation | None,
    transition: Transition,
) -> None:
    """Cancel queued work and request cancellation for the active operation."""
    # Drain the queue so no waiting operation survives the shutdown
    for pending in tuple(queue):
        queue.remove(pending)
        pending.record.cancellation_requested = True
        transition(pending, OperationState.CANCELLED, "shutdown", 0)
    # Signal the active operation only when it declared safe points
    if active is not None and active.safe_points:
        active.cancellation.set()
        active.record.cancellation_requested = True
        # Only a running operation may move to the cancelling state
        if active.record.state == OperationState.RUNNING:
            transition(
                active,
                OperationState.CANCELLING,
                active.record.progress_phase,
                active.record.progress_percent,
            )


def is_cancellable(pending: PendingOperation) -> bool:
    """Report whether the operation can still be cancelled safely."""
    # Queued work is always cancellable; running work only at its safe points
    return pending.record.state == OperationState.QUEUED or (
        pending.record.state in {OperationState.RUNNING, OperationState.CANCELLING}
        and bool(pending.safe_points)
    )

