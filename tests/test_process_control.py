"""Process-control tests for the installation mutex and launch reconciliation."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# The prototypes this module exercised are not part of this repository.
if importlib.util.find_spec("reference") is None:
    raise unittest.SkipTest("reference prototypes are not available")
from reference.prototypes.process_control import (  # noqa: E402
    InstallationMutex,
    InventorySnapshot,
    InventoryUnavailable,
    LaunchEvidence,
    MutexAlreadyHeld,
    MutexError,
    ProcessObservation,
    ReconciliationState,
    canonical_windows_path,
    installation_mutex_name,
    reconcile_processes,
)


# Fixture install root used by every reconciliation scenario
DAYZ_EXE = r"D:\Servers\Primary\DayZServer_x64.exe"


@dataclass
class FakeMutexApi:
    """Scriptable mutex API double that records created names and closed handles."""
    already_exists: bool = False
    error: OSError | None = None
    created_names: list[str] = field(default_factory=list)
    closed_handles: list[int] = field(default_factory=list)

    def create(self, name: str) -> tuple[int, bool]:
        """Return a fixed handle or raise the scripted failure, recording the name."""
        self.created_names.append(name)
        if self.error is not None:
            raise self.error
        return 41, self.already_exists

    def close(self, handle: int) -> None:
        """Record the closed handle."""
        self.closed_handles.append(handle)


@dataclass(frozen=True)
class FakeInventory:
    """Inventory double that returns a fixed snapshot or a scripted error."""
    snapshot: InventorySnapshot | None = None
    error: Exception | None = None

    def candidates(self, expected_executable: str) -> InventorySnapshot:
        """Return the scripted snapshot or raise the scripted error."""
        del expected_executable
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def observation(
    pid: int = 100,
    path: str = DAYZ_EXE,
    creation_time_ns: int | None = 123_000,
    launch_token: str | None = None,
) -> ProcessObservation:
    """Build a process observation with the standard DayZ fixture values."""
    return ProcessObservation(pid, path, creation_time_ns, launch_token)


class InstallationMutexTests(unittest.TestCase):
    """Contract: one manager instance holds the installation mutex per install root."""
    def test_name_is_stable_for_equivalent_canonical_paths(self) -> None:
        """Derive one mutex name for equivalent canonical install paths."""
        first = installation_mutex_name(r"D:\Servers\Primary\.")
        second = installation_mutex_name(r"d:\servers\primary")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(r"Local\DayZ-ServerMan-"))

    def test_acquire_and_release_close_owned_handle(self) -> None:
        """Close the owned handle exactly once across acquire and release."""
        api = FakeMutexApi()
        mutex = InstallationMutex(r"D:\Servers\Primary", api)
        mutex.acquire()
        self.assertEqual(api.created_names, [mutex.name])
        self.assertEqual(api.closed_handles, [])
        mutex.release()
        self.assertEqual(api.closed_handles, [41])
        # Releasing twice must not close the handle again
        mutex.release()
        self.assertEqual(api.closed_handles, [41])

    def test_existing_mutex_fails_closed_and_closes_transient_handle(self) -> None:
        """Fail closed when the mutex already exists and close the transient handle."""
        api = FakeMutexApi(already_exists=True)
        mutex = InstallationMutex(r"D:\Servers\Primary", api)
        with self.assertRaises(MutexAlreadyHeld):
            mutex.acquire()
        # The transient handle must be closed even on failure
        self.assertEqual(api.closed_handles, [41])

    def test_access_failure_becomes_mutex_error(self) -> None:
        """Translate an access failure into a coded mutex error."""
        api = FakeMutexApi(error=PermissionError("access denied"))
        with self.assertRaisesRegex(MutexError, "cannot acquire"):
            InstallationMutex(r"D:\Servers\Primary", api).acquire()


class LaunchEvidenceTests(unittest.TestCase):
    """Contract: only strong launch evidence marks a process as manager-owned."""
    def test_creation_time_proves_matching_launch(self) -> None:
        """Prove a launch by matching creation time."""
        evidence = LaunchEvidence.create(100, DAYZ_EXE, 123_000, "token-a")
        self.assertTrue(evidence.proves(observation()))

    def test_token_can_prove_launch_when_creation_time_is_unavailable(self) -> None:
        """Prove a launch by token when the creation time is unavailable."""
        evidence = LaunchEvidence.create(100, DAYZ_EXE, None, "token-a")
        process = observation(creation_time_ns=None, launch_token="token-a")
        self.assertTrue(evidence.proves(process))

    def test_pid_path_without_strong_discriminator_is_not_managed(self) -> None:
        """Refuse to claim a process matched only by pid and path."""
        evidence = LaunchEvidence.create(100, DAYZ_EXE, None, "token-a")
        self.assertFalse(evidence.proves(observation(creation_time_ns=None)))

    def test_mismatched_creation_time_and_token_are_not_managed(self) -> None:
        """Refuse to claim a process whose time and token both differ."""
        evidence = LaunchEvidence.create(100, DAYZ_EXE, 123_000, "token-a")
        process = observation(creation_time_ns=456_000, launch_token="token-b")
        self.assertFalse(evidence.proves(process))


class ReconciliationTests(unittest.TestCase):
    """Contract: process reconciliation fails closed when evidence is ambiguous."""
    def reconcile(
        self,
        processes: tuple[ProcessObservation, ...] = (),
        *,
        complete: bool = True,
        evidence: LaunchEvidence | None = None,
    ) -> ReconciliationState:
        """Reconcile the fixture executable against the scripted inventory."""
        inventory = FakeInventory(InventorySnapshot(processes, complete))
        return reconcile_processes(DAYZ_EXE, inventory, evidence)

    def test_complete_empty_inventory_is_stopped(self) -> None:
        """Report stopped for a complete empty inventory."""
        self.assertEqual(self.reconcile(), ReconciliationState.STOPPED)

    def test_matching_process_without_evidence_is_external(self) -> None:
        """Treat a matching process without evidence as external."""
        self.assertEqual(
            self.reconcile((observation(),)),
            ReconciliationState.RUNNING_EXTERNAL,
        )

    def test_matching_launch_evidence_is_managed(self) -> None:
        """Treat a matching launch with evidence as managed."""
        evidence = LaunchEvidence.create(100, DAYZ_EXE, 123_000, "token-a")
        self.assertEqual(
            self.reconcile((observation(),), evidence=evidence),
            ReconciliationState.RUNNING_MANAGED,
        )

    def test_stale_pid_evidence_is_external(self) -> None:
        """Treat stale pid evidence as an external process."""
        evidence = LaunchEvidence.create(100, DAYZ_EXE, 122_000, "token-a")
        self.assertEqual(
            self.reconcile((observation(),), evidence=evidence),
            ReconciliationState.RUNNING_EXTERNAL,
        )

    def test_multiple_matching_processes_are_ambiguous(self) -> None:
        """Report ambiguity when several processes match."""
        self.assertEqual(
            self.reconcile((observation(100), observation(101))),
            ReconciliationState.AMBIGUOUS,
        )

    def test_unrelated_process_is_ignored(self) -> None:
        """Ignore unrelated executables entirely."""
        other = observation(path=r"D:\Other\DayZServer_x64.exe")
        self.assertEqual(self.reconcile((other,)), ReconciliationState.STOPPED)

    def test_incomplete_inventory_fails_closed(self) -> None:
        """Fail closed on an incomplete inventory."""
        self.assertEqual(
            self.reconcile((observation(),), complete=False),
            ReconciliationState.UNKNOWN,
        )

    def test_access_denied_fails_closed(self) -> None:
        """Fail closed when the inventory is access denied."""
        inventory = FakeInventory(error=PermissionError("access denied"))
        self.assertEqual(
            reconcile_processes(DAYZ_EXE, inventory),
            ReconciliationState.UNKNOWN,
        )

    def test_inventory_failure_fails_closed(self) -> None:
        """Fail closed when the inventory snapshot is unavailable."""
        inventory = FakeInventory(error=InventoryUnavailable("snapshot failed"))
        self.assertEqual(
            reconcile_processes(DAYZ_EXE, inventory),
            ReconciliationState.UNKNOWN,
        )

    def test_case_only_path_difference_matches(self) -> None:
        """Match a process whose path differs only by case."""
        # Uppercase the canonical path to simulate a case-only variant
        process = observation(path=canonical_windows_path(DAYZ_EXE).upper())
        self.assertEqual(
            self.reconcile((process,)),
            ReconciliationState.RUNNING_EXTERNAL,
        )


if __name__ == "__main__":
    unittest.main()
