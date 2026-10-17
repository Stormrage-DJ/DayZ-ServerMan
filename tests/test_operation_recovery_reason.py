"""The recovery reason of a lane failure (8.11 finding F1, Architect 2026-10-17 14:48:26, decision 4 item 3).

A recovery-required failure that names a recovery reason blocks the lane with that reason. Without one, the
block keeps the safe message, so every other lane kind is unchanged. The record keeps the safe message.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationFailure, OperationState
from dayz_serverman.application.operations.store import OperationStore

# The safe message of the failures in these tests
MESSAGE = "Commit step message."


def failing(**options: str) -> Any:
    """Return work that fails with recovery required and the given owner or reason."""
    def work(_context: Any) -> None:
        raise OperationFailure("RECOVERY_REQUIRED", MESSAGE, recovery_required=True, **options)
    return work


class RecoveryReasonTests(unittest.TestCase):
    """The lane block of a recovery-required failure, with and without a recovery reason."""

    def setUp(self) -> None:
        """Create a lane over a temporary operations root."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_recovery_reason_")
        self.addCleanup(temporary.cleanup)
        self.lane = OperationManager(OperationStore(Path(temporary.name) / "operations"))
        self.addCleanup(self.lane.shutdown, 2)

    def finished(self, kind: str, work: Any) -> Any:
        """Submit the work and return its record once it ends with RECOVERY_REQUIRED."""
        operation_id = self.lane.submit(kind, work).operation_id
        for _ in range(500):
            record = self.lane.get(operation_id)
            if record.state is OperationState.RECOVERY_REQUIRED:
                return record
            time.sleep(0.01)
        raise AssertionError("the operation did not end RECOVERY_REQUIRED")

    def blocks(self) -> list[tuple[str, str | None]]:
        """Return the reason and owner of every lane block."""
        return [entry[:2] for entry in self.lane._recovery_blocks._entries]

    def test_the_field_defaults_to_none(self) -> None:
        """A failure without the field has no recovery reason."""
        self.assertIsNone(OperationFailure("RECOVERY_REQUIRED", MESSAGE).recovery_reason)
        self.assertEqual(OperationFailure("X", MESSAGE, recovery_reason="Reason.").recovery_reason, "Reason.")

    def test_without_a_reason_the_block_keeps_the_safe_message(self) -> None:
        """Every other lane kind: the safe message, under the kind of the operation or the named owner."""
        record = self.finished("PLAIN", failing())
        self.assertEqual((self.blocks(), record.terminal_error.message), ([(MESSAGE, "PLAIN")], MESSAGE))
        self.lane.clear_recovery_block("PLAIN")
        self.finished("OWNED", failing(recovery_owner="OWNER"))
        self.assertEqual(self.blocks(), [(MESSAGE, "OWNER")])

    def test_a_reason_names_the_one_block_and_the_record_keeps_the_message(self) -> None:
        """The block carries the reason under the named owner; the record keeps the safe message."""
        record = self.finished("NAMED", failing(recovery_owner="OWNER", recovery_reason="Block reason."))
        self.assertEqual(self.blocks(), [("Block reason.", "OWNER")])
        self.assertEqual(self.lane.recovery_block_pair(), ("Block reason.", "OWNER"))
        self.assertEqual(record.terminal_error.message, MESSAGE)


if __name__ == "__main__":
    unittest.main()
