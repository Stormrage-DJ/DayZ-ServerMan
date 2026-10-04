"""Bounded FIFO single-worker operation manager."""
from __future__ import annotations

import threading
import uuid
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from .context import OperationContext
from .drain import cancel_for_shutdown, is_cancellable
from .event_history import EventHistory
from .execution import execute_operation
from .logging import OperationLog
from .models import (
    OperationEvent,
    OperationNotCancellable,
    OperationNotFound,
    OperationRecord,
    OperationState,
    PendingOperation,
    QueueUnavailable,
    RECOVERY_BLOCK,
    SHUTTING_DOWN,
    utc_now,
)
from .publication import OperationPublication
from .recovery_blocks import RecoveryBlockAccess, RecoveryBlocks
from .store import OperationStore
from ...observability.structured_log import StructuredLogger, current_correlation_id


# Signature of a unit of work executed by the operation lane
OperationWork = Callable[["OperationContext"], Mapping[str, Any] | None]


class OperationManager(RecoveryBlockAccess):
    """Serialize exclusive mutations through one bounded FIFO worker lane."""
    def __init__(
        self,
        store: OperationStore,
        *,
        queue_limit: int = 8,
        event_limit: int = 256,
        event_age_limit_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """Configure queue, history, clock, and logging for the lane."""
        # Bound the queue so the lane cannot accumulate unbounded work
        if not 1 <= queue_limit <= 32:
            raise ValueError("queue_limit must be from 1 through 32")
        self._store = store
        self._queue_limit = queue_limit
        self._event_history = EventHistory(
            count_limit=event_limit,
            age_limit_seconds=event_age_limit_seconds,
            clock=clock or time.monotonic,
        )
        self._queue: deque[PendingOperation] = deque()
        self._operations: dict[str, PendingOperation] = {}
        # One re-entrant lock guards queue, records, worker, and recovery state
        self._condition = threading.Condition(threading.RLock())
        # Identify this session so event cursors stay scoped to one process run
        self._session_id = uuid.uuid4().hex
        self._accepting = True
        self._stopping = False
        self._active: PendingOperation | None = None
        self._worker: threading.Thread | None = None
        self._log = OperationLog(logger)
        self._recovery_blocks = RecoveryBlocks(self._condition, self._log)
        # Progress, evidence, detail and transitions are published in one place
        self._publication = OperationPublication(
            store, self._event_history, self._log, self._session_id,
            self._condition, clock or time.monotonic,
        )

    @property
    def session_id(self) -> str:
        """Return the identifier of the current operation session."""
        return self._session_id

    def submit(
        self,
        kind: str,
        work: OperationWork,
        *,
        safe_points: frozenset[str] = frozenset(),
        log_fields: Mapping[str, Any] | None = None,
        target_profile_id: str | None = None,
    ) -> OperationRecord:
        """Accept new work, enqueue it, and return the accepted record.

        The submitter names the profile that the operation belongs to, if any.
        """
        with self._condition:
            # Refuse new work while recovery or shutdown blocks the lane
            reason, owner = self._recovery_blocks.latest()
            if reason is not None:
                raise QueueUnavailable(reason, RECOVERY_BLOCK, owner)
            if not self._accepting:
                raise QueueUnavailable("operation lane is draining", SHUTTING_DOWN)
            if len(self._queue) >= self._queue_limit:
                raise QueueUnavailable("operation queue is full")
            # Start the worker on first submission so idle lanes stay quiet
            self._ensure_worker()
            operation_id = uuid.uuid4().hex
            record = OperationRecord(
                operation_id, kind, OperationState.ACCEPTED, utc_now(),
                target_profile_id=target_profile_id,
            )
            # Capture the caller correlation so events stay traceable to the request
            pending = PendingOperation(
                record,
                work,
                safe_points,
                current_correlation_id(),
                dict(log_fields or {}),
            )
            self._operations[operation_id] = pending
            self._persist_and_emit(pending, "state", {"state": record.state.value})
            # Move the record to queued before the worker can see the entry
            self._transition(pending, OperationState.QUEUED, "queued", 0)
            self._queue.append(pending)
            self._condition.notify()
            return record.snapshot()

    def get(self, operation_id: str) -> OperationRecord:
        """Return a snapshot of the record for the given operation identifier."""
        with self._condition:
            pending = self._operations.get(operation_id)
            if pending is None:
                raise OperationNotFound("operation was not found")
            return pending.record.snapshot()

    def list_recent(self) -> tuple[OperationRecord, ...]:
        """Return snapshots of every operation tracked in this session."""
        with self._condition:
            return tuple(item.record.snapshot() for item in self._operations.values())

    def request_cancellation(self, operation_id: str) -> OperationRecord:
        """Cancel a queued operation or request cancellation of a running one."""
        with self._condition:
            pending = self._operations.get(operation_id)
            if pending is None:
                raise OperationNotFound("operation was not found")
            record = pending.record
            # Remove queued work before the worker can start it
            if record.state == OperationState.QUEUED:
                self._queue.remove(pending)
                record.cancellation_requested = True
                self._transition(pending, OperationState.CANCELLED, "cancelled", 0)
            # Running work cancels only at declared safe points
            elif record.state == OperationState.RUNNING:
                if not pending.safe_points:
                    raise OperationNotCancellable("operation has no safe cancellation point")
                # Signal cooperation and mark the record as cancelling
                pending.cancellation.set()
                record.cancellation_requested = True
                self._transition(
                    pending,
                    OperationState.CANCELLING,
                    record.progress_phase,
                    record.progress_percent,
                )
            # A repeated request for an already cancelling operation is a no-op
            elif record.state == OperationState.CANCELLING:
                pass
            else:
                raise OperationNotCancellable("operation is not cancellable in its current state")
            return record.snapshot()

    def read_events(
        self,
        after_sequence: int,
        maximum: int = 100,
    ) -> tuple[tuple[OperationEvent, ...], int]:
        """Return events after a cursor with the cursor for the next read."""
        # Reject cursors and page sizes outside the supported range
        if after_sequence < 0 or not 1 <= maximum <= 500:
            raise ValueError("event cursor or maximum is invalid")
        with self._condition:
            return self._event_history.read(after_sequence, maximum, self._publication.sequence)

    def shutdown(self, timeout: float | None = None) -> bool:
        """Begin shutdown and wait until the lane drains within the timeout."""
        self.begin_shutdown()
        return self.wait_for_drain(timeout)

    def begin_shutdown(self) -> None:
        """Stop accepting work and cancel or flag everything outstanding."""
        with self._condition:
            # Make repeated shutdown calls idempotent
            if self._stopping:
                return
            self._accepting = False
            self._stopping = True
            # Cancel queued work and flag the active operation for cancellation
            cancel_for_shutdown(self._queue, self._active, self._transition)
            self._condition.notify_all()
        # Report the drain outside the lock to keep the critical section short
        self._log.emit("operation_lane.draining", fields={"queued_cancelled": True})

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Wait for the worker to finish and report whether the lane drained."""
        # Snapshot the worker under the lock so the join does not hold it
        with self._condition:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def is_drained(self) -> bool:
        """Report whether no operation is active or waiting."""
        with self._condition:
            return self._active is None and not self._queue

    def is_cancellable(self, operation_id: str) -> bool:
        """Report cancellability for the operation with the given identifier."""
        with self._condition:
            pending = self._operations.get(operation_id)
            if pending is None:
                raise OperationNotFound("operation was not found")
            return is_cancellable(pending)

    def _ensure_worker(self) -> None:
        """Start the single worker thread once, on demand."""
        if self._worker is not None:
            return
        # Run the lane as a daemon so a stuck operation cannot block interpreter exit
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="dayz-serverman-mutation-lane",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        """Process queued operations one at a time until shutdown drains the lane."""
        while True:
            with self._condition:
                # Block until work arrives or shutdown begins
                while not self._queue and not self._stopping:
                    self._condition.wait()
                # Exit once shutdown has drained the last queued operation
                if not self._queue:
                    return
                pending = self._queue.popleft()
                # Skip work that was cancelled while it waited in the queue
                if pending.record.state == OperationState.CANCELLED:
                    continue
                self._active = pending
                self._transition(pending, OperationState.RUNNING, "running", 0)
            # Execute outside the lock so progress checkpoints can acquire it
            execute_operation(
                pending,
                OperationContext(self, pending),
                self._condition,
                self._transition,
            )
            with self._condition:
                # Release the lane and wake drain waiters
                self._active = None
                self._condition.notify_all()

    def _transition(
        self,
        pending: PendingOperation,
        state: OperationState,
        phase: str,
        percent: int,
    ) -> None:
        """Apply one legal state transition and publish the resulting record."""
        self._publication.transition(pending, state, phase, percent)

    def _persist_and_emit(
        self,
        pending: PendingOperation,
        kind: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Persist the record and append a sequenced event to store and history."""
        self._publication.persist_and_emit(pending, kind, payload)
