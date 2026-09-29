"""Legal operation state transitions and internal diagnostics."""

from __future__ import annotations

from .models import OperationState


# Single source of truth for the allowed operation state changes
LEGAL_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.ACCEPTED: frozenset((OperationState.QUEUED,)),
    OperationState.QUEUED: frozenset(
        (OperationState.RUNNING, OperationState.CANCELLED)
    ),
    OperationState.RUNNING: frozenset(
        (
            OperationState.CANCELLING,
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
            OperationState.RECOVERY_REQUIRED,
        )
    ),
    OperationState.CANCELLING: frozenset(
        (
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
            OperationState.RECOVERY_REQUIRED,
        )
    ),
    OperationState.SUCCEEDED: frozenset(),
    OperationState.FAILED: frozenset(),
    OperationState.CANCELLED: frozenset(),
    OperationState.RECOVERY_REQUIRED: frozenset(),
}


class IllegalOperationTransition(RuntimeError):
    """Internal diagnostic for a rejected state transition."""

    code = "INTERNAL_FAILURE"
    safe_message = "The operation state transition is invalid."

    def __init__(self, current: OperationState, requested: OperationState) -> None:
        """Capture the current and requested states for diagnostics."""
        self.current = current
        self.requested = requested
        self.diagnostic = f"illegal operation transition: {current.value}->{requested.value}"
        super().__init__(self.diagnostic)


def require_legal_transition(
    current: OperationState,
    requested: OperationState,
) -> None:
    """Raise when the requested transition is not legal from the current state."""
    if requested not in LEGAL_TRANSITIONS[current]:
        raise IllegalOperationTransition(current, requested)
