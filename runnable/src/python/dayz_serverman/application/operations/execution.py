"""Operation result normalization and terminal-state mapping."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from .context import OperationContext
from .models import (
    OperationCancelled,
    OperationError,
    OperationFailure,
    OperationState,
    PendingOperation,
)


# Callback signature for publishing an operation state transition
Transition = Callable[[PendingOperation, OperationState, str, int], None]


def execute_operation(
    pending: PendingOperation,
    context: OperationContext,
    condition: threading.Condition,
    transition: Transition,
) -> None:
    """Run pending work and map its outcome onto a terminal state."""
    try:
        # Run the work through the cooperative context
        result = pending.work(context)
        # Normalize an absent result to an empty payload
        normalized_result = dict(result) if result is not None else {}
        # Verify the payload stays JSON-serializable before publishing it
        json.dumps(normalized_result, ensure_ascii=False, allow_nan=False)
        # Publish the result together with the success state
        with condition:
            pending.record.result = normalized_result
            transition(pending, OperationState.SUCCEEDED, "complete", 100)
    except OperationCancelled:
        # Map cooperative cancellation to the cancelled terminal state
        with condition:
            transition(
                pending,
                OperationState.CANCELLED,
                "cancelled",
                pending.record.progress_percent,
            )
    except OperationFailure as error:
        # Block the lane when the failure requires supervised recovery
        if error.recovery_required:
            context.block_for_recovery(error.safe_message)
        # Record the safe error before publishing the failure state
        with condition:
            pending.record.terminal_error = OperationError(
                error.code,
                error.safe_message,
                error.retryable,
            )
            state = (
                OperationState.RECOVERY_REQUIRED
                if error.recovery_required
                else OperationState.FAILED
            )
            transition(pending, state, "failed", pending.record.progress_percent)
    except Exception:
        # Convert unexpected faults into a stable internal failure
        with condition:
            pending.record.terminal_error = OperationError(
                "INTERNAL_FAILURE",
                "The operation could not be completed.",
            )
            transition(
                pending,
                OperationState.FAILED,
                "failed",
                pending.record.progress_percent,
            )
