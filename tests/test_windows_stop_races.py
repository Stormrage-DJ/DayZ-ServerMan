"""Verify bounded post-stop observation without weakening initial ownership checks."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"runnable/src/python"))
from dayz_serverman.adapters.windows.graceful_stop import WindowsGracefulStop
from dayz_serverman.domain.lifecycle import InventorySnapshot, LaunchEvidence, LifecycleFailure, ProcessObservation, canonical_process_path


class StopObservationRaceTests(unittest.TestCase):
    """Only fresh proven absence may complete a stop after transient unknown observations."""

    def adapter(self, snapshots, ticks):
        """Inject deterministic process observations and a bounded monotonic clock."""
        inventory=Mock()
        inventory.candidates=Mock(side_effect=snapshots)
        runner=Mock(return_value=subprocess.CompletedProcess([],0,"",""))
        adapter=WindowsGracefulStop(inventory,runner=runner,clock=Mock(side_effect=ticks),
                                   sleeper=Mock(),timeout_seconds=1)
        return adapter,runner

    def test_unknown_during_exit_waits_for_fresh_proven_absence(self):
        """Send one stop request and wait through the exiting process's unreadable interval."""
        evidence=LaunchEvidence(321,canonical_process_path("DayZServer_x64.exe"),100,"token","handle")
        running=InventorySnapshot((ProcessObservation(321,"DayZServer_x64.exe",100),))
        adapter,runner=self.adapter([running,InventorySnapshot((),False),InventorySnapshot(())],[0,0,0.5])
        adapter.request_stop(evidence)
        runner.assert_called_once()

    def test_persistent_unknown_remains_a_failure_at_deadline(self):
        """Never equate an inaccessible live process with a successful stop."""
        evidence=LaunchEvidence(321,canonical_process_path("DayZServer_x64.exe"),100,"token","handle")
        running=InventorySnapshot((ProcessObservation(321,"DayZServer_x64.exe",100),))
        adapter,runner=self.adapter([running,InventorySnapshot((),False)],[0,0,1])
        with self.assertRaises(LifecycleFailure) as raised:
            adapter.request_stop(evidence)
        self.assertEqual(raised.exception.code,"PROCESS_STATE_UNKNOWN")
        runner.assert_called_once()

    def test_unproven_initial_ownership_never_sends_stop(self):
        """An incomplete preflight inventory blocks signalling entirely."""
        evidence=LaunchEvidence(321,canonical_process_path("DayZServer_x64.exe"),100,"token","handle")
        adapter,runner=self.adapter([InventorySnapshot((),False)],[])
        with self.assertRaises(LifecycleFailure):
            adapter.request_stop(evidence)
        runner.assert_not_called()

    def test_reused_process_identity_during_wait_fails_immediately(self):
        """A replaced process is an ownership failure, not transient uncertainty."""
        evidence=LaunchEvidence(321,canonical_process_path("DayZServer_x64.exe"),100,"token","handle")
        running=InventorySnapshot((ProcessObservation(321,"DayZServer_x64.exe",100),))
        changed=InventorySnapshot((ProcessObservation(321,"DayZServer_x64.exe",200),))
        adapter,runner=self.adapter([running,changed],[0,0])
        with self.assertRaises(LifecycleFailure) as raised:
            adapter.request_stop(evidence)
        self.assertEqual(raised.exception.code,"PROCESS_OWNERSHIP_UNPROVEN")
        runner.assert_called_once()


if __name__=="__main__":
    unittest.main()
