"""Reconciliation tests mapping process observations to managed server states."""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.reconciliation import reconcile_server  # noqa: E402
from dayz_serverman.domain.lifecycle import (  # noqa: E402
    InventorySnapshot,
    LaunchEvidence,
    ProcessObservation,
    ServerState,
    canonical_process_path,
)


EXECUTABLE = r"D:\Synthetic DayZ\DayZServer_x64.exe"


@dataclass
class FakeInventory:
    """Scripted inventory port returning a fixed snapshot or raising an error."""
    snapshot: InventorySnapshot | None = None
    error: Exception | None = None

    def candidates(self, expected_executable: str) -> InventorySnapshot:
        """Return the fixed snapshot or raise the scripted error."""
        del expected_executable
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def observation(
    pid: int = 42,
    path: str = EXECUTABLE,
    creation: int | None = 100,
    token: str | None = None,
) -> ProcessObservation:
    """Build a process observation with defaults matching the synthetic launch."""
    return ProcessObservation(pid, path, creation, token)


def evidence(
    pid: int = 42,
    creation: int | None = 100,
    handle: str | None = None,
) -> LaunchEvidence:
    """Build launch evidence for the synthetic executable and launch token."""
    return LaunchEvidence(pid, canonical_process_path(EXECUTABLE), creation, "launch-a", handle)


class LifecycleReconciliationTests(unittest.TestCase):
    """Ownership proof rules for mapping processes to server states."""
    def reconcile(
        self,
        processes: tuple[ProcessObservation, ...],
        *,
        complete: bool = True,
        launch_evidence: LaunchEvidence | None = None,
        held: bool = False,
    ):
        """Reconcile the synthetic executable against observations and evidence."""
        inventory = FakeInventory(InventorySnapshot(processes, complete))
        return reconcile_server(
            EXECUTABLE,
            inventory,
            launch_evidence,
            lambda _token, _pid: held,
        )

    def test_empty_complete_inventory_is_stopped(self) -> None:
        """An empty complete inventory reconciles to the stopped state."""
        self.assertEqual(self.reconcile(()).state, ServerState.STOPPED)

    def test_pid_and_path_without_strong_evidence_is_external(self) -> None:
        """A matching process without strong evidence is treated as external."""
        result = self.reconcile((observation(),))
        self.assertEqual(result.state, ServerState.RUNNING_EXTERNAL)
        self.assertEqual(result.diagnostic_code, "PROCESS_OWNERSHIP_UNPROVEN")

    def test_creation_time_token_or_held_handle_can_prove_current_launch(self) -> None:
        """Creation time, launch token, or a held handle alone can prove ownership."""
        # Each proof channel alone must establish managed ownership
        creation = self.reconcile((observation(),), launch_evidence=evidence())
        token = self.reconcile(
            (observation(creation=None, token="launch-a"),),
            launch_evidence=evidence(creation=None),
        )
        handle = self.reconcile(
            (observation(creation=None),),
            launch_evidence=evidence(creation=None, handle="handle-a"),
            held=True,
        )
        self.assertEqual(
            [creation.state, token.state, handle.state],
            [ServerState.RUNNING_MANAGED] * 3,
        )

    def test_stale_creation_evidence_never_proves_ownership(self) -> None:
        """Stale creation evidence never proves ownership of the observed process."""
        result = self.reconcile(
            (observation(creation=101),),
            launch_evidence=evidence(creation=100),
        )
        self.assertEqual(result.state, ServerState.RUNNING_EXTERNAL)

    def test_ambiguity_and_incomplete_or_failed_inventory_fail_closed(self) -> None:
        """Ambiguous, incomplete, or failed inventory results fail closed."""
        ambiguous = self.reconcile((observation(42), observation(43)))
        incomplete = self.reconcile((observation(),), complete=False)
        # A failing inventory must also fail closed
        failed = reconcile_server(
            EXECUTABLE,
            FakeInventory(error=PermissionError("denied")),
            None,
            lambda _token, _pid: False,
        )
        self.assertEqual(ambiguous.state, ServerState.AMBIGUOUS)
        self.assertEqual(incomplete.state, ServerState.UNKNOWN)
        self.assertEqual(failed.state, ServerState.UNKNOWN)

    def test_unrelated_same_name_location_is_not_a_match(self) -> None:
        """A same-named executable in another folder is not a match."""
        result = self.reconcile((observation(path=r"D:\Other\DayZServer_x64.exe"),))
        self.assertEqual(result.state, ServerState.STOPPED)


if __name__ == "__main__":
    unittest.main()

