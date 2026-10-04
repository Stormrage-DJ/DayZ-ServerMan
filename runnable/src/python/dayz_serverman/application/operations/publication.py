"""Publication of operation record changes: progress, evidence, detail and transitions."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .event_history import EventHistory
from .logging import OperationLog
from .models import (
    OperationEvent,
    OperationState,
    PendingOperation,
    TERMINAL_STATES,
    utc_now,
)
from .store import OperationStore
from .transitions import require_legal_transition

# A record carries at most this many per-item detail entries
DETAIL_ITEM_LIMIT = 200
# The detail of one operation is published at most once in this many seconds
DETAIL_INTERVAL_SECONDS = 1.0
# Exact field set of one detail entry
_DETAIL_FIELDS = ("workshop_id", "phase", "done_bytes", "total_bytes")


def normalize_detail(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the bounded detail value: at most 200 entries with the exact field set."""
    entries: list[dict[str, Any]] = []
    for item in items:
        if len(entries) >= DETAIL_ITEM_LIMIT:
            break
        entry = {name: item.get(name) for name in _DETAIL_FIELDS}
        # Identity and phase are text; a byte count is a non-negative integer or null
        if not isinstance(entry["workshop_id"], str) or not isinstance(entry["phase"], str):
            raise ValueError("progress detail entry is invalid")
        for name in ("done_bytes", "total_bytes"):
            value = entry[name]
            if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError("progress detail byte count is invalid")
        entries.append(entry)
    return {"items": entries}


class OperationPublication:
    """Persist and publish every change of an operation record under the lane lock."""

    def __init__(
        self, store: OperationStore, history: EventHistory, log: OperationLog,
        session_id: str, lock: threading.Condition, clock: Callable[[], float],
    ) -> None:
        """Store the persistence, event, logging and timing collaborators."""
        self._store = store
        self._history = history
        self._log = log
        self._session_id = session_id
        self._lock = lock
        self._clock = clock
        self.sequence = 0
        # Detail values waiting for their publication time, and the last publication times
        self._held_detail: dict[str, dict[str, Any]] = {}
        self._detail_published: dict[str, float] = {}

    def progress(self, pending: PendingOperation, phase: str, percent: int) -> None:
        """Record a progress checkpoint and publish it to readers."""
        with self._lock:
            record = pending.record
            record.progress_phase = phase
            record.progress_percent = percent
            # Remember the working phase; a terminal transition never overwrites it
            record.last_working_phase = phase
            # A held detail value that is due travels with the checkpoint
            self._take_due_detail(pending)
            # Bump the revision so polling readers can detect the change
            record.revision += 1
            self.persist_and_emit(
                pending,
                "progress",
                {"phase": phase, "progress_percent": percent},
            )
            self._log.emit(
                "operation.progress",
                pending,
                {"phase": phase, "progress_percent": percent},
                level="DEBUG",
            )

    def evidence(self, pending: PendingOperation, evidence: Mapping[str, object]) -> None:
        """Store bounded evidence on the record before an external wait."""
        with self._lock:
            # Copy the evidence so later mutation cannot alter the record
            pending.record.result = dict(evidence)
            pending.record.revision += 1
            self.persist_and_emit(pending, "evidence", {"recorded": True})

    def detail(self, pending: PendingOperation, items: Iterable[Mapping[str, Any]]) -> bool:
        """Offer a per-item detail value; return whether it was published now.

        At most one value per second is published; a value offered earlier is
        held and replaced by a later one, so the last value wins.
        """
        value = normalize_detail(items)
        with self._lock:
            operation_id = pending.record.operation_id
            self._held_detail[operation_id] = value
            if not self._take_due_detail(pending):
                return False
            # A publication increments the revision and reaches polling readers
            pending.record.revision += 1
            self.persist_and_emit(
                pending, "progress_detail", {"item_count": len(value["items"])},
            )
            return True

    def transition(
        self, pending: PendingOperation, state: OperationState, phase: str, percent: int,
    ) -> None:
        """Apply one legal state transition and publish the resulting record."""
        record = pending.record
        # Refuse transitions the state machine does not allow
        require_legal_transition(record.state, state)
        record.state = state
        record.progress_phase = phase
        record.progress_percent = percent
        record.revision += 1
        # Stamp the run boundaries as the operation advances
        if state == OperationState.RUNNING:
            record.started_at = utc_now()
        if state in TERMINAL_STATES:
            record.finished_at = utc_now()
            # The last offered detail value stays on the terminal record
            held = self._held_detail.pop(record.operation_id, None)
            self._detail_published.pop(record.operation_id, None)
            if held is not None:
                record.progress_detail = held
        # Persist and publish the new state before logging it
        self.persist_and_emit(pending, "state", {"state": state.value})
        # Failures and recovery blocks are the only error-level transitions
        if state in {OperationState.FAILED, OperationState.RECOVERY_REQUIRED}:
            level = "ERROR"
        elif state in TERMINAL_STATES:
            level = "INFO"
        else:
            level = "DEBUG"
        fields: dict[str, Any] = {
            **pending.log_fields,
            "kind": record.kind,
            "state": state.value,
            "phase": phase,
            "last_working_phase": record.last_working_phase,
        }
        # Surface the child process id when the result carries one
        if record.result is not None and isinstance(record.result.get("process_id"), int):
            fields["child_process_id"] = record.result["process_id"]
        if record.terminal_error is not None:
            fields["error_code"] = record.terminal_error.code
            fields["error_message"] = record.terminal_error.message
        self._log.emit("operation.state", pending, fields, level=level)

    def persist_and_emit(
        self, pending: PendingOperation, kind: str, payload: Mapping[str, Any],
    ) -> None:
        """Persist the record and append a sequenced event to store and history."""
        self._store.save_record(pending.record)
        # Advance the monotonic sequence so event cursors stay ordered
        self.sequence += 1
        event = OperationEvent(
            self._session_id,
            self.sequence,
            pending.record.operation_id,
            utc_now(),
            kind,
            dict(payload),
        )
        # Persist the event before publishing it to polling readers
        self._store.append_event(event)
        self._history.append(event)

    def _take_due_detail(self, pending: PendingOperation) -> bool:
        """Move a held detail value onto the record when the interval has passed."""
        operation_id = pending.record.operation_id
        held = self._held_detail.get(operation_id)
        if held is None:
            return False
        now = self._clock()
        last = self._detail_published.get(operation_id)
        if last is not None and now - last < DETAIL_INTERVAL_SECONDS:
            return False
        pending.record.progress_detail = self._held_detail.pop(operation_id)
        self._detail_published[operation_id] = now
        return True
