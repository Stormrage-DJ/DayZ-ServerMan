"""Restore operation tests for strict contracts, cancellation, and recovery blocking."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationState, TERMINAL_STATES  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.restore_coordinator import RestoreCoordinator  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorageError  # noqa: E402


PARAMETERS = {
    "profile_id": "main",
    "backup_id": "backup-1",
    "expected_profile_revision": 3,
    "expected_settings_revision": 4,
    "expected_manifest_digest": "a" * 64,
    "preview_fingerprint": "b" * 64,
}


class FakeRestoreService:
    """Restore service double with configurable apply behavior."""
    def __init__(self, behavior=None) -> None:
        """Store the optional behavior used by apply."""
        self.behavior = behavior

    def preview(self, profile_id, backup_id):
        """Return a minimal preview payload for the requested backup."""
        return {"profile_id": profile_id, "backup_id": backup_id}

    def inspect_recovery(self):
        """Report recovery as unblocked so the coordinator can proceed."""
        return {"blocked": False, "diagnostics": []}

    def apply(self, *_args):
        """Run the configured behavior or return a default operation result."""
        operation_id, checkpoint = _args[-2:]
        # Delegate to the injected behavior when one is set
        if self.behavior:
            return self.behavior(operation_id, checkpoint)
        return {"operation_id": operation_id}


class RestoreOperationTests(unittest.TestCase):
    """Restore coordinator contracts for strict requests, recovery, and shutdown."""
    def setUp(self) -> None:
        """Create an isolated operation store per test."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_restore_ops_")
        self.operations = OperationManager(OperationStore(Path(self.temporary.name) / "operations"))

    def tearDown(self) -> None:
        """Shut down the manager and remove the temporary store."""
        self.operations.shutdown(2)
        self.temporary.cleanup()

    def wait_terminal(self, operation_id: str):
        """Block until the operation reaches a terminal state or fail the test."""
        deadline = time.monotonic() + 2
        # Poll until the operation leaves the running states
        while time.monotonic() < deadline:
            current = self.operations.get(operation_id)
            if current.state in TERMINAL_STATES:
                return current
            time.sleep(0.01)
        raise AssertionError("restore operation did not finish")

    def test_contract_is_strict_and_recovery_failure_blocks_new_mutations(self) -> None:
        """Recovery uncertainty blocks new mutations while queries stay open."""
        def uncertain(_operation_id, _checkpoint):
            """Fail the apply with a recovery-required storage error."""
            raise RestoreStorageError(
                "RECOVERY_REQUIRED", "Synthetic recovery uncertainty.", recovery_required=True,
            )
        coordinator = RestoreCoordinator(FakeRestoreService(uncertain), self.operations)  # type: ignore[arg-type]
        # Unknown request fields are rejected before anything is queued
        with self.assertRaises(ApplicationCallError):
            coordinator.apply_restore({**PARAMETERS, "path": "D:\\unsafe"})
        # The accepted run ends in RECOVERY_REQUIRED and installs a recovery block
        accepted = coordinator.apply_restore(PARAMETERS)
        current = self.wait_terminal(accepted["operation_id"])
        self.assertEqual(current.state, OperationState.RECOVERY_REQUIRED)
        self.assertEqual(current.terminal_error.code, "RECOVERY_REQUIRED")
        self.assertIsNotNone(self.operations.recovery_block)
        # The pending recovery block turns further mutations into conflicts
        with self.assertRaises(ApplicationCallError) as blocked:
            coordinator.apply_restore(PARAMETERS)
        self.assertEqual(blocked.exception.code.value, "MUTATION_CONFLICT")
        self.assertEqual(coordinator.inspect_restore_recovery({})["blocked"], False)

    def test_review_digests_are_canonical_before_queueing(self) -> None:
        """Malformed review digests are rejected before the restore is queued."""
        coordinator = RestoreCoordinator(FakeRestoreService(), self.operations)  # type: ignore[arg-type]
        # Wrong length, uppercase, non-hex, whitespace, and type variants must all fail
        invalid = ("a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 63 + "\n", None, 42)
        # Both digest fields validate canonical lowercase hex
        for field in ("expected_manifest_digest", "preview_fingerprint"):
            for value in invalid:
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaises(ApplicationCallError) as raised:
                        coordinator.apply_restore({**PARAMETERS, field: value})
                    self.assertEqual(raised.exception.code.value, "INVALID_REQUEST")
                    self.assertFalse(raised.exception.retryable)
        self.assertEqual(self.operations.list_recent(), ())

    def test_cancellation_at_declared_safe_point(self) -> None:
        """Cancellation is honored at the declared safe point."""
        entered, release = threading.Event(), threading.Event()
        def cancellable(_operation_id, checkpoint):
            """Wait for cancellation, then report a safe checkpoint."""
            entered.set()
            release.wait(2)
            checkpoint("STAGE_TARGETS", 35)
            return {}
        coordinator = RestoreCoordinator(FakeRestoreService(cancellable), self.operations)  # type: ignore[arg-type]
        # Start the run and wait until the apply section is entered
        accepted = coordinator.apply_restore(PARAMETERS)
        self.assertTrue(entered.wait(1))
        # Request cancellation, then let the safe point run
        self.operations.request_cancellation(accepted["operation_id"])
        release.set()
        self.assertEqual(
            self.wait_terminal(accepted["operation_id"]).state,
            OperationState.CANCELLED,
        )

    def test_shutdown_waits_for_non_cancellable_publication_section(self) -> None:
        """Shutdown waits for the non-cancellable publication section."""
        publishing, release = threading.Event(), threading.Event()
        def publication(_operation_id, checkpoint):
            """Report the journal checkpoint, then hold the publication open."""
            checkpoint("WRITE_JOURNAL", 60)
            publishing.set()
            release.wait(2)
            return {"journal_state": "COMMITTED"}
        coordinator = RestoreCoordinator(FakeRestoreService(publication), self.operations)  # type: ignore[arg-type]
        accepted = coordinator.apply_restore(PARAMETERS)
        self.assertTrue(publishing.wait(1))
        # Shutdown must not drain while the publication section is open
        self.operations.begin_shutdown()
        self.assertFalse(self.operations.wait_for_drain(0.05))
        # Drain completes once the publication releases
        release.set()
        self.assertTrue(self.operations.wait_for_drain(2))
        self.assertEqual(
            self.operations.get(accepted["operation_id"]).state,
            OperationState.SUCCEEDED,
        )

    def test_startup_invalid_journal_blocks_mutations_but_keeps_queries(self) -> None:
        """An invalid startup journal blocks mutations but keeps queries available."""
        # Seed a corrupt restore journal in a fresh manager data directory
        manager = Path(self.temporary.name) / "startup-manager"
        journals = manager / "data" / "operations" / "restore-journals"
        journals.mkdir(parents=True)
        (journals / "broken.json").write_text("{broken", encoding="utf-8")
        composition = build_composition(manager)
        try:
            # Queries stay available and report the unresolved recovery
            snapshot = composition.coordinator.get_application_snapshot({})
            self.assertIn("unresolved restore recovery", snapshot["mutation_block"])
            inspection = composition.restore_coordinator.inspect_restore_recovery({})
            self.assertTrue(inspection["blocked"])
            # Mutations fail closed while the recovery is unresolved
            with self.assertRaises(ApplicationCallError) as blocked:
                composition.coordinator.save_settings({
                    "expected_revision": None,
                    "dayz_root": None,
                    "steamcmd_root": None,
                    "custom_backup_root": None,
                })
            self.assertEqual(blocked.exception.code.value, "MUTATION_CONFLICT")
        finally:
            composition.operations.shutdown(2)

    def test_storage_exception_and_unknown_fields_do_not_expose_paths_or_secrets(self) -> None:
        """Storage failures and unknown fields never leak paths or secrets."""
        # Path-shaped token that must not appear in any surfaced error
        secret = str(Path(self.temporary.name) / "SECRET-restore-token")
        def unsafe_failure(_operation_id, _checkpoint):
            """Fail the apply with an error that embeds the secret path."""
            raise OSError(f"failed at {secret}")
        coordinator = RestoreCoordinator(FakeRestoreService(unsafe_failure), self.operations)  # type: ignore[arg-type]
        # The raw OSError collapses into a sanitized storage failure
        accepted = coordinator.apply_restore(PARAMETERS)
        current = self.wait_terminal(accepted["operation_id"])
        self.assertEqual(current.terminal_error.code, "STORAGE_FAILURE")
        self.assertEqual(current.terminal_error.message, "Restore storage failed.")
        self.assertNotIn(secret, str(current.to_dict()))
        # Unknown request fields are rejected without echoing the caller value
        with self.assertRaises(ApplicationCallError) as invalid:
            coordinator.preview_restore({
                "profile_id": "main", "backup_id": "backup-1", "path": secret,
            })
        self.assertNotIn(secret, invalid.exception.safe_message)


if __name__ == "__main__":
    unittest.main()
