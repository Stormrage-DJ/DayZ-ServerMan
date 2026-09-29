"""Regression tests for event retention and illegal or terminal transitions."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    EventCursorExpired,
    OperationRecord,
    OperationState,
    PendingOperation,
    utc_now,
)
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.operations.transitions import (  # noqa: E402
    IllegalOperationTransition,
)


class FakeClock:
    """Deterministic clock whose time only advances when a test moves it."""
    def __init__(self) -> None:
        """Start the clock at a fixed offset."""
        self.value = 1000.0

    def __call__(self) -> float:
        """Return the current fixed time."""
        return self.value

    def advance(self, seconds: float) -> None:
        """Move the clock forward by the requested seconds."""
        self.value += seconds


class OperationReworkTests(unittest.TestCase):
    """Contract: retention boundaries and rejected transitions leave evidence untouched."""
    def setUp(self) -> None:
        """Create a temporary operations root for the manager."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_rework_")
        self.operations_root = Path(self.temporary.name) / "data" / "operations"

    def tearDown(self) -> None:
        """Remove the temporary operations root."""
        self.temporary.cleanup()

    def test_event_age_boundary_is_retained_then_expires_without_sleep(self) -> None:
        """Keep an event until its age limit, then expire its cursor."""
        # Persist one event and read its cursor before advancing time
        clock = FakeClock()
        manager = OperationManager(
            OperationStore(self.operations_root),
            event_limit=16,
            event_age_limit_seconds=60,
            clock=clock,
        )
        record = OperationRecord("aged-event", "TEST", OperationState.ACCEPTED, utc_now())
        pending = PendingOperation(record, lambda _context: {}, frozenset())
        manager._persist_and_emit(pending, "state", {"state": record.state.value})
        current_sequence = manager.read_events(0)[1]

        # The event is still retained exactly at its age limit
        clock.advance(60)
        retained, _ = manager.read_events(0)
        self.assertEqual(len(retained), 1)

        # One more millisecond expires the cursor for the oldest event
        clock.advance(0.001)
        with self.assertRaises(EventCursorExpired):
            manager.read_events(0)
        self.assertEqual(manager.read_events(current_sequence), ((), current_sequence))

    def test_illegal_transition_preserves_record_and_durable_bytes(self) -> None:
        """Preserve the record and durable bytes when a transition is illegal."""
        store = OperationStore(self.operations_root)
        manager = OperationManager(store)
        # Build and persist an accepted record
        record = OperationRecord(
            "illegal-transition",
            "TEST",
            OperationState.ACCEPTED,
            utc_now(),
        )
        pending = PendingOperation(record, lambda _context: {}, frozenset())
        manager._persist_and_emit(pending, "state", {"state": record.state.value})
        record_path = self.operations_root / "illegal-transition.json"
        event_path = self.operations_root / "events.jsonl"
        record_before = record.snapshot()
        record_bytes_before = record_path.read_bytes()
        event_bytes_before = event_path.read_bytes()

        # The illegal transition must be rejected without touching evidence
        with self.assertRaises(IllegalOperationTransition) as raised:
            manager._transition(pending, OperationState.SUCCEEDED, "complete", 100)

        self.assertEqual(raised.exception.code, "INTERNAL_FAILURE")
        self.assertIn("ACCEPTED->SUCCEEDED", raised.exception.diagnostic)
        self.assertEqual(record, record_before)
        self.assertEqual(record_path.read_bytes(), record_bytes_before)
        self.assertEqual(event_path.read_bytes(), event_bytes_before)

    def test_terminal_transition_is_rejected_without_persistence(self) -> None:
        """Reject a transition from a terminal record without persisting."""
        # Persist a terminal record with finished metadata
        store = OperationStore(self.operations_root)
        manager = OperationManager(store)
        record = OperationRecord(
            "terminal-transition",
            "TEST",
            OperationState.SUCCEEDED,
            utc_now(),
            revision=3,
            finished_at=utc_now(),
            progress_percent=100,
            progress_phase="complete",
        )
        pending = PendingOperation(record, lambda _context: {}, frozenset())
        store.save_record(record)
        record_path = self.operations_root / "terminal-transition.json"
        before = record_path.read_bytes()

        # The rejected transition must not change the record or write events
        with self.assertRaises(IllegalOperationTransition):
            manager._transition(pending, OperationState.FAILED, "failed", 100)

        self.assertEqual(record.state, OperationState.SUCCEEDED)
        self.assertEqual(record.revision, 3)
        self.assertEqual(record_path.read_bytes(), before)
        self.assertFalse(store.events_path.exists())


if __name__ == "__main__":
    unittest.main()
