"""Operation records, events, and stable control failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from threading import Event
from typing import Any, Mapping


def utc_now() -> str:
    """Return the current UTC time as a millisecond-precision ISO string."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class OperationState(str, Enum):
    """Lifecycle states an operation passes through on the lane."""
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


# States from which the operation can never transition again
TERMINAL_STATES = frozenset(
    (
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.RECOVERY_REQUIRED,
    )
)


@dataclass(frozen=True)
class OperationError:
    """Safe failure information exposed to callers."""
    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the wire representation of the error."""
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


@dataclass
class OperationRecord:
    """Mutable lifecycle record for one submitted operation."""
    operation_id: str
    kind: str
    state: OperationState
    accepted_at: str
    revision: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    cancellation_requested: bool = False
    progress_percent: int = 0
    progress_phase: str = "accepted"
    result: Mapping[str, Any] | None = None
    terminal_error: OperationError | None = None
    # Advisory per-item progress: null or {"items": [...]} with at most 200 entries
    progress_detail: Mapping[str, Any] | None = None
    # Profile that the submitter named for this operation; None when it names none
    target_profile_id: str | None = None
    # Phase of the last checkpoint; kept when a terminal state replaces progress_phase
    last_working_phase: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned wire representation of the record."""
        return {
            "schema_version": 1,
            "revision": self.revision,
            "operation_id": self.operation_id,
            "kind": self.kind,
            "state": self.state.value,
            "accepted_at": self.accepted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancellation_requested": self.cancellation_requested,
            "progress_percent": self.progress_percent,
            "progress_phase": self.progress_phase,
            "result": dict(self.result) if self.result is not None else None,
            "terminal_error": (
                self.terminal_error.to_dict() if self.terminal_error is not None else None
            ),
            "progress_detail": _detail_copy(self.progress_detail),
            "target_profile_id": self.target_profile_id,
            "last_working_phase": self.last_working_phase,
        }

    def snapshot(self) -> OperationRecord:
        """Return a detached copy that readers may keep outside the lock."""
        return OperationRecord(
            operation_id=self.operation_id,
            kind=self.kind,
            state=self.state,
            accepted_at=self.accepted_at,
            revision=self.revision,
            started_at=self.started_at,
            finished_at=self.finished_at,
            cancellation_requested=self.cancellation_requested,
            progress_percent=self.progress_percent,
            progress_phase=self.progress_phase,
            result=dict(self.result) if self.result is not None else None,
            terminal_error=self.terminal_error,
            progress_detail=_detail_copy(self.progress_detail),
            target_profile_id=self.target_profile_id,
            last_working_phase=self.last_working_phase,
        )


def _detail_copy(detail: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a detached copy of a progress detail value, or None."""
    if detail is None:
        return None
    return {"items": [dict(item) for item in detail["items"]]}


@dataclass(frozen=True)
class OperationEvent:
    """Immutable event recorded for one operation-lane occurrence."""
    session_id: str
    sequence: int
    operation_id: str
    occurred_at: str
    kind: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-versioned wire representation of the event."""
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "operation_id": self.operation_id,
            "occurred_at": self.occurred_at,
            "kind": self.kind,
            "payload": dict(self.payload),
        }


# Machine-readable causes of a refused submission; the bridge reports one as error detail "reason"
QUEUE_FULL = "QUEUE_FULL"
RECOVERY_BLOCK = "RECOVERY_BLOCK"
SHUTTING_DOWN = "SHUTTING_DOWN"


class QueueUnavailable(RuntimeError):
    """Raised when the operation lane cannot accept new work."""
    def __init__(self, message: str, reason: str = QUEUE_FULL, owner: str | None = None) -> None:
        """Store the safe message, the machine-readable cause, and the owner of a recovery block."""
        self.reason = reason
        self.owner = owner
        super().__init__(message)

    @property
    def details(self) -> dict[str, str]:
        """Return the additive error detail that names the cause, and the block owner when there is one."""
        return {"reason": self.reason, **({"owner": self.owner} if self.owner else {})}


class OperationNotFound(RuntimeError):
    """Raised when an operation identifier is unknown to the manager."""
    pass


class OperationNotCancellable(RuntimeError):
    """Raised when the operation cannot be cancelled in its current state."""
    pass


class EventCursorExpired(RuntimeError):
    """Raised when an event cursor fell out of the retained history."""
    pass


class OperationCancelled(RuntimeError):
    """Raised inside operation work at a safe point after cancellation."""
    pass


class OperationFailure(RuntimeError):
    """Report a safe, categorized failure raised by operation work."""
    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        recovery_required: bool = False,
    ) -> None:
        """Store the failure code, safe message, and retry or recovery flags."""
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.recovery_required = recovery_required
        super().__init__(safe_message)


@dataclass
class PendingOperation:
    """One submitted operation: record, work, cancellation, and logging state."""
    record: OperationRecord
    work: Any
    safe_points: frozenset[str]
    correlation_id: str | None = None
    log_fields: Mapping[str, Any] = field(default_factory=dict)
    cancellation: Event = field(default_factory=Event)
