"""Check shutdown races without weakening unreadable-process ownership guards."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.adapters.windows.processes import WindowsProcessInventory
from dayz_serverman.domain.lifecycle import ProcessObservation


class ProcessInventoryRaceTests(unittest.TestCase):
    """An observation failure requires fresh absence proof before reporting a complete scan."""

    def inventory(self, snapshots, observations):
        """Inject Toolhelp and identity observations without binding native Win32 calls."""
        inventory = WindowsProcessInventory.__new__(WindowsProcessInventory)
        inventory._candidate_process_ids = Mock(side_effect=snapshots)
        inventory._observe = Mock(side_effect=observations)
        return inventory

    def test_exited_process_is_absent_in_fresh_snapshot(self) -> None:
        """A process disappearing during shutdown does not leave a false unknown state."""
        inventory = self.inventory([(321,), ()], [PermissionError("process exited")])
        result = inventory.candidates("DayZServer_x64.exe")
        self.assertTrue(result.complete)
        self.assertEqual(result.processes, ())

    def test_unreadable_live_process_remains_incomplete(self) -> None:
        """Access denial on a still-present candidate must block destructive operations."""
        inventory = self.inventory([(321,), (321,)], [PermissionError("access denied")])
        self.assertFalse(inventory.candidates("DayZServer_x64.exe").complete)

    def test_unobserved_replacement_process_remains_incomplete(self) -> None:
        """A new candidate appearing during the race cannot be ignored."""
        inventory = self.inventory([(321,), (654,)], [OSError("process exited")])
        self.assertFalse(inventory.candidates("DayZServer_x64.exe").complete)

    def test_surviving_observed_process_keeps_its_identity(self) -> None:
        """Preserve other proven candidates when an unrelated candidate exits."""
        observation = ProcessObservation(654, "DayZServer_x64.exe", 100)
        inventory = self.inventory([(321, 654), (654,)], [OSError("exited"), observation])
        result = inventory.candidates("DayZServer_x64.exe")
        self.assertTrue(result.complete)
        self.assertEqual(result.processes, (observation,))

    def test_failed_second_snapshot_does_not_prove_absence(self) -> None:
        """Inventory failures propagate instead of authorizing a stopped result."""
        inventory = self.inventory([(321,), OSError("snapshot failed")], [OSError("exited")])
        with self.assertRaises(OSError):
            inventory.candidates("DayZServer_x64.exe")


if __name__ == "__main__":
    unittest.main()
