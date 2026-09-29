"""Failure classification tests for terminal backup operation errors."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backup_coordinator import BackupCoordinator  # noqa: E402
from dayz_serverman.application.backups import BackupService  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationState, TERMINAL_STATES  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.domain.profiles import validate_profile_id  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage  # noqa: E402
from dayz_serverman.repositories.backup_archives import (  # noqa: E402
    BackupArchiveError,
    verify_archive as real_verify_archive,
)
from dayz_serverman.repositories.backup_verification import (  # noqa: E402
    BackupVerificationError,
)
from tests.test_backups import FakeProfiles, FakeSettings, create_runtime_profile, record  # noqa: E402


class ValidatingProfiles(FakeProfiles):
    """Profile port that validates identifiers before delegating reads."""
    def read(self, profile_id: object):
        """Validate the identifier, then return the stored profile record."""
        validate_profile_id(profile_id)
        return super().read(profile_id)


class BackupErrorClassificationTests(unittest.TestCase):
    """Storage, request, and recovery error classifications for backups."""
    def setUp(self) -> None:
        """Create the dayz fixture tree and the backup destination."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_backup_errors_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "dayz"
        config = self.dayz / "Config Files" / "serverDZ.cfg"
        config.parent.mkdir(parents=True)
        config.write_text("fixture", encoding="utf-8")
        create_runtime_profile(self.dayz)
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.operations: list[OperationManager] = []

    def tearDown(self) -> None:
        """Shut down tracked operations and remove the temporary tree."""
        for operations in self.operations:
            operations.shutdown(2)
        self.temporary.cleanup()

    def coordinator(self, storage: BackupStorage) -> BackupCoordinator:
        """Build a coordinator with its own operation manager and store."""
        service = BackupService(
            ValidatingProfiles(record(False)),  # type: ignore[arg-type]
            FakeSettings(self.dayz, self.backups),  # type: ignore[arg-type]
            storage,
            clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
            identifier=lambda: f"error-{len(self.operations)}",
        )
        operations = OperationManager(OperationStore(self.root / f"operations-{len(self.operations)}"))
        self.operations.append(operations)
        return BackupCoordinator(service, operations)

    def run_operation(self, coordinator: BackupCoordinator, profile_id: str = "main"):
        """Run one backup request to a terminal state and return its record."""
        accepted = coordinator.create_backup({
            "profile_id": profile_id,
            "expected_profile_revision": 3,
            "expected_settings_revision": 4,
        })
        operations = self.operations[-1]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = operations.get(accepted["operation_id"])
            if current.state in TERMINAL_STATES:
                return current
            time.sleep(0.01)
        raise AssertionError("backup operation did not finish")

    def assert_storage_failure(self, current: object) -> None:
        """Assert the record failed with a retryable storage error."""
        self.assertEqual(current.state, OperationState.FAILED)
        self.assertEqual(current.terminal_error.code, "STORAGE_FAILURE")
        self.assertTrue(current.terminal_error.retryable)

    def test_staging_manifest_corruption_is_retryable_storage_failure(self) -> None:
        """A corrupted staged manifest is a retryable storage failure."""
        def corrupt(phase: str) -> None:
            """Rewrite the staged manifest revision before publication."""
            if phase == "WRITE_MANIFEST":
                path = next(self.backups.glob(".staging-*/manifest.json"))
                value = json.loads(path.read_text(encoding="utf-8"))
                value["profile_revision"] = 99
                path.write_text(json.dumps(value), encoding="utf-8")

        current = self.run_operation(self.coordinator(BackupStorage(phase_hook=corrupt)))
        self.assert_storage_failure(current)

    def test_staged_payload_verification_is_retryable_storage_failure(self) -> None:
        """A staged payload verification error is a retryable storage failure."""
        with patch(
            "dayz_serverman.repositories.backups.verify_directory",
            side_effect=BackupVerificationError("synthetic staged verification"),
        ):
            current = self.run_operation(self.coordinator(BackupStorage()))
        self.assert_storage_failure(current)

    def test_invalid_operator_profile_remains_invalid_request(self) -> None:
        """An invalid operator profile id is a non-retryable invalid request."""
        current = self.run_operation(self.coordinator(BackupStorage()), "bad/profile")
        self.assertEqual(current.state, OperationState.FAILED)
        self.assertEqual(current.terminal_error.code, "INVALID_REQUEST")
        self.assertFalse(current.terminal_error.retryable)

    def test_postpublication_verification_remains_recovery_required(self) -> None:
        """A failure after publication classifies as recovery required."""
        calls = 0
        def fail_second(archive: Path, manifest: object) -> None:
            """Fail only the post-publication archive verification call."""
            nonlocal calls
            calls += 1
            if calls == 2:
                raise BackupArchiveError("synthetic published verification")
            real_verify_archive(archive, manifest)  # type: ignore[arg-type]

        with patch("dayz_serverman.repositories.backups.verify_archive", side_effect=fail_second):
            current = self.run_operation(self.coordinator(BackupStorage()))
        self.assertEqual(current.state, OperationState.RECOVERY_REQUIRED)
        self.assertEqual(current.terminal_error.code, "RECOVERY_REQUIRED")
        self.assertFalse(current.terminal_error.retryable)


if __name__ == "__main__":
    unittest.main()
