"""The lane logs the lift of a recovery block only when a block is removed (8.12 Architect decision 1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.observability.structured_log import StructuredLogger

CLEARED = "operation_lane.recovery_block_cleared"
EDITOR = ("Mutations are blocked by unresolved mission map recovery.", "APPLY_MISSION_MAP")
RESTORE = ("Committed restore targets could not be proven.", "RESTORE_BACKUP")


class RecoveryBlockClearedLogTests(unittest.TestCase):
    """One INFO line per real removal, with the owner and the removed reasons; nothing for an empty clear."""

    def setUp(self) -> None:
        """Create a lane with a manager log below a disposable root."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_block_cleared_")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.log = root / "manager.jsonl"
        self.lane = OperationManager(OperationStore(root / "operations"), logger=StructuredLogger(self.log))
        self.addCleanup(self.lane.shutdown, 2)

    def cleared(self) -> list[tuple[str, dict]]:
        """Return the level and fields of every lift line, in order."""
        if not self.log.exists():
            return []
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [(record["level"], record["fields"]) for record in records if record["event"] == CLEARED]

    def test_owner_clear_logs_once_with_its_reasons(self) -> None:
        """Clearing an existing owner logs its reasons once; the other owner's block stays."""
        self.lane.block_for_recovery(*RESTORE)
        self.lane.block_for_recovery(*EDITOR)
        self.lane.clear_recovery_block(EDITOR[1])
        self.assertEqual(self.cleared(), [("INFO", {"owner": EDITOR[1], "reasons": [EDITOR[0]]})])
        self.assertEqual(self.lane.recovery_block_pair(), RESTORE)

    def test_clear_with_nothing_to_remove_logs_nothing(self) -> None:
        """No block, or no block of that owner, writes no lift line."""
        self.lane.clear_recovery_block(EDITOR[1])
        self.lane.block_for_recovery(*RESTORE)
        self.lane.clear_recovery_block(EDITOR[1])
        self.assertEqual(self.cleared(), [])

    def test_full_clear_names_every_reason_and_no_owner(self) -> None:
        """A clear without owner removes every block and names all of their reasons."""
        self.lane.block_for_recovery(*RESTORE)
        self.lane.block_for_recovery(*EDITOR)
        self.lane.clear_recovery_block()
        self.lane.clear_recovery_block()
        self.assertEqual(self.cleared(), [("INFO", {"reasons": [RESTORE[0], EDITOR[0]]})])
        self.assertIsNone(self.lane.recovery_block)


if __name__ == "__main__":
    unittest.main()
