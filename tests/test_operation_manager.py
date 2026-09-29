"""Operation manager tests for the FIFO lane, cancellation, queue bounds, and durability."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    EventCursorExpired,
    OperationFailure,
    OperationNotCancellable,
    OperationState,
    QueueUnavailable,
)
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402


# Terminal states the wait helper accepts as finished
TERMINAL = {
    OperationState.SUCCEEDED,
    OperationState.FAILED,
    OperationState.CANCELLED,
    OperationState.RECOVERY_REQUIRED,
}


class OperationManagerTests(unittest.TestCase):
    """Contract: one mutation runs at a time, survives restarts, and cancels safely."""
    def setUp(self) -> None:
        """Create a manager over a temporary operations root."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_operations_")
        self.operations_root = Path(self.temporary.name) / "data" / "operations"
        self.manager = OperationManager(OperationStore(self.operations_root), queue_limit=4)

    def tearDown(self) -> None:
        """Shut the manager down and remove the temporary root."""
        self.manager.shutdown(2)
        self.temporary.cleanup()

    def wait_for(self, operation_id: str, states: set[OperationState], timeout: float = 2.0):
        """Block until the operation reaches one of the states or fail a timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.manager.get(operation_id)
            if record.state in states:
                return record
            time.sleep(0.01)
        raise AssertionError(f"operation did not reach {states}")

    def test_fifo_lane_runs_one_mutation_at_a_time(self) -> None:
        """Run FIFO submissions strictly one at a time in submission order."""
        # Track start order and the peak number of concurrent tasks
        first_started = threading.Event()
        release_first = threading.Event()
        order: list[str] = []
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def work(name: str, block: bool = False):
            """Return a task that records its order and optionally blocks."""
            def run(_context):
                """Track concurrency, record the name, and optionally wait for release."""
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    order.append(name)
                if block:
                    first_started.set()
                    release_first.wait(2)
                with lock:
                    active -= 1
                return {"name": name}

            return run

        # Queue three tasks while the first task is blocked
        first = self.manager.submit("FIRST", work("first", True))
        first_started.wait(1)
        second = self.manager.submit("SECOND", work("second"))
        third = self.manager.submit("THIRD", work("third"))
        release_first.set()
        for operation in (first, second, third):
            self.wait_for(operation.operation_id, TERMINAL)
        # Only one task may run at a time, in submission order
        self.assertEqual(order, ["first", "second", "third"])
        self.assertEqual(maximum_active, 1)

    def test_queue_bound_rejects_extra_mutation(self) -> None:
        """Reject mutations beyond the configured queue bound."""
        manager = OperationManager(OperationStore(self.operations_root / "bounded"), queue_limit=1)
        started = threading.Event()
        release = threading.Event()
        # Fill the lane and its single queue slot, then expect rejection
        try:
            manager.submit("ACTIVE", lambda _ctx: (started.set(), release.wait(2), {})[-1])
            started.wait(1)
            manager.submit("QUEUED", lambda _ctx: {})
            with self.assertRaises(QueueUnavailable):
                manager.submit("EXTRA", lambda _ctx: {})
        finally:
            release.set()
            manager.shutdown(2)

    def test_running_cancellation_waits_for_declared_safe_point(self) -> None:
        """Cancel a running operation only at its declared safe point."""
        # Block the task until the test releases it at its safe point
        started = threading.Event()
        proceed = threading.Event()

        def work(context):
            """Signal readiness, wait for release, then check the safe point."""
            started.set()
            proceed.wait(2)
            context.checkpoint("safe-copy-boundary", 50)
            return {}

        operation = self.manager.submit(
            "CANCELLABLE",
            work,
            safe_points=frozenset(("safe-copy-boundary",)),
        )
        started.wait(1)
        cancelling = self.manager.request_cancellation(operation.operation_id)
        self.assertEqual(cancelling.state, OperationState.CANCELLING)
        # Releasing the task lets it reach the safe point and cancel
        proceed.set()
        final = self.wait_for(operation.operation_id, TERMINAL)
        self.assertEqual(final.state, OperationState.CANCELLED)

    def test_running_operation_without_safe_points_cannot_cancel(self) -> None:
        """Refuse cancellation for a running operation without safe points."""
        # Submit without safe points so cancellation must be refused
        started = threading.Event()
        release = threading.Event()
        operation = self.manager.submit(
            "NON_CANCELLABLE",
            lambda _context: (started.set(), release.wait(2), {})[-1],
        )
        started.wait(1)
        with self.assertRaises(OperationNotCancellable):
            self.manager.request_cancellation(operation.operation_id)
        release.set()
        # The operation still finishes successfully after the release
        self.assertEqual(
            self.wait_for(operation.operation_id, TERMINAL).state,
            OperationState.SUCCEEDED,
        )

    def test_queued_operation_cancels_without_running(self) -> None:
        """Cancel a queued operation before it ever runs."""
        # Occupy the lane so the second operation stays queued
        started = threading.Event()
        release = threading.Event()
        self.manager.submit(
            "ACTIVE",
            lambda _context: (started.set(), release.wait(2), {})[-1],
        )
        started.wait(1)
        ran = threading.Event()
        queued = self.manager.submit("QUEUED", lambda _context: (ran.set(), {})[-1])
        # The queued operation cancels without ever running
        cancelled = self.manager.request_cancellation(queued.operation_id)
        release.set()
        self.assertEqual(cancelled.state, OperationState.CANCELLED)
        self.assertFalse(ran.is_set())

    def test_failures_are_sanitized_and_recovery_is_explicit(self) -> None:
        """Sanitize failures and surface recovery-required failures explicitly."""
        # A raw exception is sanitized before it is reported
        raw = self.manager.submit(
            "RAW_FAILURE",
            lambda _context: (_ for _ in ()).throw(RuntimeError("secret detail")),
        )
        raw_final = self.wait_for(raw.operation_id, TERMINAL)
        self.assertEqual(raw_final.state, OperationState.FAILED)
        self.assertNotIn("secret", raw_final.terminal_error.message)

        # An unpersistable result becomes an internal failure
        invalid_result = self.manager.submit(
            "INVALID_RESULT",
            lambda _context: {"not_json": {1, 2}},
        )
        invalid_final = self.wait_for(invalid_result.operation_id, TERMINAL)
        self.assertEqual(invalid_final.state, OperationState.FAILED)
        self.assertEqual(invalid_final.terminal_error.code, "INTERNAL_FAILURE")

        # A recovery-required failure keeps its explicit state
        recovery = self.manager.submit(
            "RECOVERY",
            lambda _context: (_ for _ in ()).throw(
                OperationFailure(
                    "RECOVERY_REQUIRED",
                    "Manual recovery is required.",
                    recovery_required=True,
                )
            ),
        )
        recovery_final = self.wait_for(recovery.operation_id, TERMINAL)
        self.assertEqual(recovery_final.state, OperationState.RECOVERY_REQUIRED)

    def test_records_and_events_are_durable(self) -> None:
        """Persist operation records and an append-only event log."""
        # Run one operation to success
        operation = self.manager.submit("DURABLE", lambda _context: {"saved": True})
        final = self.wait_for(operation.operation_id, TERMINAL)
        # The record file and the append-only event log agree
        record_path = self.operations_root / f"{operation.operation_id}.json"
        stored = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["state"], OperationState.SUCCEEDED.value)
        self.assertEqual(stored["revision"], final.revision)
        lines = (self.operations_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 3)
        self.assertTrue(all(json.loads(line)["schema_version"] == 1 for line in lines))

    def test_expired_cursor_requires_snapshot_reload(self) -> None:
        """Require a snapshot reload when the event cursor expires."""
        manager = OperationManager(
            OperationStore(self.operations_root / "cursor"),
            event_limit=2,
        )
        try:
            # Emit enough events to expire the oldest cursor
            operation = manager.submit("EVENTFUL", lambda context: (context.checkpoint("mid", 50), {})[-1])
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and manager.get(operation.operation_id).state not in TERMINAL:
                time.sleep(0.01)
            # The expired cursor must demand a snapshot reload
            with self.assertRaises(EventCursorExpired):
                manager.read_events(0)
        finally:
            manager.shutdown(2)

    def test_future_cursor_is_rejected(self) -> None:
        """Reject an event cursor beyond the newest event."""
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.manager.read_events(1)

    def test_shutdown_cancels_queue_and_drains_active_at_safe_point(self) -> None:
        """Cancel the queue on shutdown and drain the active operation at its safe point."""
        # Block the active operation until shutdown asks it to drain
        started = threading.Event()
        proceed = threading.Event()

        def active_work(context):
            """Signal start, wait for release, then checkpoint for shutdown."""
            started.set()
            proceed.wait(2)
            context.checkpoint("safe-shutdown", 50)
            return {}

        active = self.manager.submit(
            "ACTIVE",
            active_work,
            safe_points=frozenset(("safe-shutdown",)),
        )
        started.wait(1)
        queued = self.manager.submit("QUEUED", lambda _context: {})
        # A zero timeout cannot drain yet, so the queued operation cancels
        self.assertFalse(self.manager.shutdown(0.01))
        self.assertEqual(self.manager.get(queued.operation_id).state, OperationState.CANCELLED)
        # Releasing the safe point lets shutdown succeed and cancel the active task
        proceed.set()
        self.assertTrue(self.manager.shutdown(2))
        self.assertEqual(self.manager.get(active.operation_id).state, OperationState.CANCELLED)


if __name__ == "__main__":
    unittest.main()
