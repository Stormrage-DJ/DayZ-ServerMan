"""Advisory per-item progress detail on the operation record: shape, cap, pace, persistence."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.coordinator import ApplicationCoordinator  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import (  # noqa: E402
    OperationRecord,
    OperationState,
    PendingOperation,
    utc_now,
)
from dayz_serverman.application.operations.publication import (  # noqa: E402
    DETAIL_ITEM_LIMIT,
    normalize_detail,
)
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402


class _Clock:
    """Deterministic clock that moves only when the test moves it."""

    def __init__(self) -> None:
        """Start at a fixed offset."""
        self.value = 500.0

    def __call__(self) -> float:
        """Return the current fixed time."""
        return self.value


def entry(workshop_id: str = "111", phase: str = "verify_source", done=None, total=None):
    """Return one detail entry."""
    return {"workshop_id": workshop_id, "phase": phase,
            "done_bytes": done, "total_bytes": total}


class ProgressDetailTests(unittest.TestCase):
    """Design section 11: bounded, paced, revision-counted and kept after the end."""

    def setUp(self) -> None:
        """Create a manager with a fake clock and one running record."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_detail_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "operations"
        self.clock = _Clock()
        self.manager = OperationManager(OperationStore(self.root), clock=self.clock)
        self.addCleanup(self.manager.shutdown, 2)
        self.record = OperationRecord("detail-op", "TEST", OperationState.RUNNING, utc_now())
        self.pending = PendingOperation(self.record, lambda _context: {}, frozenset())
        self.publication = self.manager._publication

    def stored(self) -> dict:
        """Return the persisted record."""
        return json.loads((self.root / "detail-op.json").read_text(encoding="utf-8"))

    def test_record_starts_without_detail(self) -> None:
        """A record that never published detail reports null."""
        self.assertIsNone(self.record.to_dict()["progress_detail"])
        self.assertIsNone(self.record.snapshot().progress_detail)

    def test_shape_keeps_exactly_the_four_fields(self) -> None:
        """Unknown fields are dropped; missing byte counts become null."""
        value = normalize_detail([{"workshop_id": "111", "phase": "done", "done_bytes": 4,
                                   "total_bytes": 4, "path": "C:/secret"},
                                  {"workshop_id": "222", "phase": "queued"}])
        self.assertEqual(value, {"items": [entry("111", "done", 4, 4), entry("222", "queued")]})

    def test_malformed_entries_are_refused(self) -> None:
        """A wrong identity, phase or byte count is an error, never a stored value."""
        for bad in ({"workshop_id": 111, "phase": "x"}, {"workshop_id": "1", "phase": None},
                    entry(done=-1), entry(total=True), entry(done=1.5)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_detail([bad])

    def test_at_most_200_items_are_kept(self) -> None:
        """Entries above the cap are dropped."""
        self.assertEqual(DETAIL_ITEM_LIMIT, 200)
        self.assertTrue(self.publication.detail(
            self.pending, [entry(str(index + 1)) for index in range(250)]))
        items = self.record.progress_detail["items"]
        self.assertEqual(len(items), 200)
        self.assertEqual((items[0]["workshop_id"], items[-1]["workshop_id"]), ("1", "200"))

    def test_publication_increments_the_revision_and_persists(self) -> None:
        """The first value is published at once, with a new revision and an event."""
        before = self.record.revision
        self.assertTrue(self.publication.detail(self.pending, [entry(done=1, total=9)]))
        self.assertEqual(self.record.revision, before + 1)
        self.assertEqual(self.stored()["progress_detail"], {"items": [entry(done=1, total=9)]})
        events, _cursor = self.manager.read_events(0)
        self.assertEqual((events[-1].kind, events[-1].payload),
                         ("progress_detail", {"item_count": 1}))

    def test_at_most_one_publication_per_second_and_last_value_wins(self) -> None:
        """Values offered inside the second are held; the newest one is published later."""
        self.publication.detail(self.pending, [entry(done=1)])
        revision = self.record.revision
        self.clock.value += 0.25
        self.assertFalse(self.publication.detail(self.pending, [entry(done=2)]))
        self.clock.value += 0.5
        self.assertFalse(self.publication.detail(self.pending, [entry(done=3)]))
        # Nothing was published inside the second
        self.assertEqual(self.record.revision, revision)
        self.assertEqual(self.record.progress_detail["items"][0]["done_bytes"], 1)
        self.clock.value += 0.25
        self.assertTrue(self.publication.detail(self.pending, [entry(done=4)]))
        self.assertEqual(self.record.revision, revision + 1)
        self.assertEqual(self.stored()["progress_detail"]["items"][0]["done_bytes"], 4)

    def test_held_value_travels_with_a_due_checkpoint(self) -> None:
        """A checkpoint after the interval carries the held value; an early one does not."""
        self.publication.detail(self.pending, [entry(done=1)])
        self.publication.detail(self.pending, [entry(done=2)])
        self.publication.progress(self.pending, "verify_source", 10)
        self.assertEqual(self.record.progress_detail["items"][0]["done_bytes"], 1)
        self.clock.value += 1.0
        self.publication.progress(self.pending, "verify_target", 20)
        self.assertEqual(self.stored()["progress_detail"]["items"][0]["done_bytes"], 2)

    def test_detail_stays_after_the_terminal_state_with_the_last_value(self) -> None:
        """The terminal record keeps the detail, including a value still held."""
        self.publication.detail(self.pending, [entry(phase="verify_source")])
        self.publication.detail(self.pending, [entry(phase="done", done=9, total=9)])
        self.manager._transition(self.pending, OperationState.SUCCEEDED, "complete", 100)
        expected = {"items": [entry(phase="done", done=9, total=9)]}
        self.assertEqual(self.record.progress_detail, expected)
        self.assertEqual(self.stored()["progress_detail"], expected)
        self.assertEqual(self.stored()["state"], "SUCCEEDED")


class ProgressDetailLaneTests(unittest.TestCase):
    """The operation context publishes detail and `get_operation` exposes it."""

    def test_get_operation_gains_progress_detail(self) -> None:
        """Work on the lane publishes detail; the bridge value carries it additively."""
        with tempfile.TemporaryDirectory(prefix="serverman_detail_") as temporary:
            manager = OperationManager(OperationStore(Path(temporary) / "operations"))
            self.addCleanup(manager.shutdown, 2)
            coordinator = ApplicationCoordinator(None, manager, None)

            def work(context) -> dict:
                """Publish one detail value and finish."""
                context.publish_detail([entry("111", "done", 5, 5)])
                return {}

            plain = manager.submit("TEST", lambda _context: {})
            detailed = manager.submit("TEST", work)
            deadline = time.monotonic() + 3
            while (manager.get(detailed.operation_id).state != OperationState.SUCCEEDED
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            value = coordinator.get_operation({"operation_id": detailed.operation_id})
            self.assertEqual(value["state"], "SUCCEEDED")
            self.assertEqual(value["progress_detail"], {"items": [entry("111", "done", 5, 5)]})
            # An operation without detail reports null in the same field
            self.assertIsNone(
                coordinator.get_operation({"operation_id": plain.operation_id})["progress_detail"])
            manager.shutdown(2)


if __name__ == "__main__":
    unittest.main()
