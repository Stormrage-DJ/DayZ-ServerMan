"""Restore endpoint tests for preview, apply, compensation, and verification."""
from __future__ import annotations

import contextlib
import hashlib
import sys
import tempfile
import unittest
import warnings
import zipfile
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backups import BackupService  # noqa: E402
from dayz_serverman.application.configuration import ConfigurationService  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled  # noqa: E402
from dayz_serverman.application.restores import RestoreService  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure, LifecycleSnapshot, ServerState  # noqa: E402
from dayz_serverman.domain.models import RevisionConflict  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorageError  # noqa: E402
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorage, RestoreStorageError  # noqa: E402
from tests.test_backups import FakeProfiles, FakeSettings, record  # noqa: E402


class FakeLifecycle:
    """Lifecycle port that reports a fixed server state."""
    def __init__(self, state: ServerState = ServerState.STOPPED) -> None:
        """Store the state reported by status."""
        self.state = state

    def status(self) -> LifecycleSnapshot:
        """Return a lifecycle snapshot for the configured state."""
        return LifecycleSnapshot(self.state)


class FakeMutex:
    """Mutex port that optionally raises a synthetic control conflict."""
    def __init__(self, conflict: bool = False) -> None:
        """Store whether the guard should raise a conflict."""
        self.conflict = conflict

    @contextlib.contextmanager
    def guard(self, _dayz_root: str):
        """Raise the configured conflict or pass through."""
        if self.conflict:
            raise LifecycleFailure("CONTROL_CONFLICT", "Synthetic control conflict.")
        yield


def digest(path: Path) -> str:
    """Return the SHA-256 hex digest of the file content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RestoreTests(unittest.TestCase):
    """Restore service contracts for preview, apply, failure, and tamper handling."""
    def setUp(self) -> None:
        """Create the live tree, snapshot backup, and fake ports."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_restore_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DáyZ Root"
        self.target = self.dayz / "Config Files" / "serverDZ.cfg"
        self.target.parent.mkdir(parents=True)
        self.target.write_text('hostname = "Backup";\nmaxPlayers = 60;\n', encoding="utf-8")
        (self.dayz / "profiles" / "main").mkdir(parents=True)
        self.backups = self.root / "Portable Manager" / "backups"
        self.backups.mkdir(parents=True)
        self.recovery = self.backups / "recovery"
        self.recovery.mkdir()
        mission = self.dayz / "mpmissions" / "dayzOffline.chernarusplus"
        storage = mission / "storage_3"
        storage.mkdir(parents=True)
        self.world = storage / "players.db"
        self.world.write_bytes(b"backed-up-world")
        self.profiles = FakeProfiles(record())
        self.settings = FakeSettings(self.dayz, self.backups)
        backup_service = BackupService(
            self.profiles, self.settings, BackupStorage(),  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 9, 25, 15, 0, tzinfo=UTC),
            identifier=lambda: "restore-fixture",
        )
        created = backup_service.create("main", 3, 4, lambda _phase, _percent: None)
        self.backup_id = str(created["backup_id"])
        self.snapshot_manifest = str(created["manifest_digest"])
        self.snapshot_file = self.backups / f"{self.backup_id}.zip"
        self.source_digest = digest(self.target)
        self.target.write_text('hostname = "Live";\nmaxPlayers = 40;\n', encoding="utf-8")
        self.world.write_bytes(b"changed-world")

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def service(
        self,
        storage: RestoreStorage | None = None,
        lifecycle: FakeLifecycle | None = None,
        mutex: FakeMutex | None = None,
    ) -> RestoreService:
        """Build a restore service with optional storage, lifecycle, and mutex overrides."""
        return RestoreService(
            self.profiles, self.settings, BackupStorage(), storage or RestoreStorage(),
            RestoreJournalRepository(self.root / "operations" / "restore-journals"),
            self.recovery, lifecycle or FakeLifecycle(), mutex or FakeMutex(),
        )  # type: ignore[arg-type]

    def apply(self, service: RestoreService, preview: dict[str, object], operation_id: str = "restore-1"):
        """Apply the preview and return the result with the recorded phase order."""
        phases: list[str] = []
        result = service.apply(
            "main", self.backup_id, 3, 4, self.snapshot_manifest,
            preview["fingerprint"], operation_id,
            lambda phase, _percent: phases.append(phase),
        )
        return result, phases

    def test_preview_and_restore_verify_recovery_journal_and_preserve_source(self) -> None:
        """Preview and restore commit through the journal and preserve the snapshot."""
        service = self.service()
        preview = service.preview("main", self.backup_id)
        self.assertEqual(preview["replacement_count"], 2)
        self.assertEqual(preview["creation_count"], 0)
        self.assertEqual(preview["targets"][0]["action"], "REPLACE")
        # Capture the snapshot bytes before applying the restore
        before_source = self.snapshot_file.read_bytes()
        # Apply the preview and record the phase order
        result, phases = self.apply(service, preview)
        self.assertEqual(result["journal_state"], "COMMITTED")
        self.assertEqual(phases, [
            "VERIFY_SOURCE", "STAGE_TARGETS", "STAGE_TARGETS",
            "PREPARE_RECOVERY", "WRITE_JOURNAL",
        ])
        self.assertEqual(digest(self.target), self.source_digest)
        self.assertEqual(self.world.read_bytes(), b"backed-up-world")
        self.assertEqual(self.snapshot_file.read_bytes(), before_source)
        # The retired journal records the committed restore
        journal = next((self.root / "operations" / "restore-journals" / "completed").glob("*.json"))
        self.assertIn('"committed": true', journal.read_text(encoding="utf-8"))
        self.assertTrue(any(self.recovery.rglob("serverDZ.cfg")))

    def test_completed_restore_allows_configuration_edit_and_clean_startup(self) -> None:
        """A completed restore leaves the profile usable for later edits."""
        service = self.service()
        preview = service.preview("main", self.backup_id)
        # Restore first, then edit the live configuration
        self.apply(service, preview, "configuration-followup")
        configuration = ConfigurationService(self.profiles, self.settings)  # type: ignore[arg-type]
        loaded = configuration.load("main", "server")
        configuration.apply(
            "main", "server", 3, 4, loaded["digest"], None,
            {"hostname": "Edited after restore"}, lambda _phase, _percent: None,
        )
        self.assertFalse(self.service().inspect_recovery()["blocked"])
        self.assertIn("Edited after restore", self.target.read_text(encoding="utf-8"))

    def test_stale_target_manifest_and_revision_fail_before_mutation(self) -> None:
        """Stale targets, manifests, and revisions fail before any mutation."""
        service = self.service()
        preview = service.preview("main", self.backup_id)
        # Change the live target after preview
        before = self.target.read_bytes()
        self.target.write_text("changed after preview", encoding="utf-8")
        with self.assertRaises(RevisionConflict):
            self.apply(service, preview)
        self.assertNotEqual(self.target.read_bytes(), before)
        # Stale revisions and manifest digests also fail closed
        with self.assertRaises(RevisionConflict):
            service.apply(
                "main", self.backup_id, 2, 4, self.snapshot_manifest,
                preview["fingerprint"], "stale", lambda _phase, _percent: None,
            )
        with self.assertRaises(RevisionConflict):
            service.apply(
                "main", self.backup_id, 3, 4, "0" * 64,
                preview["fingerprint"], "manifest", lambda _phase, _percent: None,
            )

    def test_process_and_control_states_fail_closed(self) -> None:
        """Unsafe lifecycle states and control conflicts fail closed."""
        # Every unsafe lifecycle state refuses to preview
        for state, code in (
            (ServerState.RUNNING_EXTERNAL, "EXTERNAL_PROCESS"),
            (ServerState.UNKNOWN, "PROCESS_STATE_UNKNOWN"),
            (ServerState.AMBIGUOUS, "PROCESS_STATE_UNKNOWN"),
            (ServerState.RUNNING_MANAGED, "CONTROL_CONFLICT"),
        ):
            with self.subTest(state=state):
                with self.assertRaises(LifecycleFailure) as raised:
                    self.service(lifecycle=FakeLifecycle(state)).preview("main", self.backup_id)
                self.assertEqual(raised.exception.code, code)
        # A control conflict also refuses to preview
        with self.assertRaises(LifecycleFailure) as raised:
            self.service(mutex=FakeMutex(True)).preview("main", self.backup_id)
        self.assertEqual(raised.exception.code, "CONTROL_CONFLICT")

    def test_failure_after_publication_compensates_or_requires_recovery(self) -> None:
        """A post-publication fault compensates or requires recovery."""
        # Record the pre-restore digest for comparison
        before = digest(self.target)
        def fail_after(phase: str, _index: int) -> None:
            """Fail after publishing so compensation must run."""
            if phase == "AFTER_PUBLISH":
                raise OSError("synthetic publication failure")
        service = self.service(RestoreStorage(fault_hook=fail_after))
        preview = service.preview("main", self.backup_id)
        with self.assertRaisesRegex(RestoreStorageError, "prior files were restored") as restored:
            self.apply(service, preview, "compensated")
        self.assertFalse(restored.exception.recovery_required)
        self.assertEqual(digest(self.target), before)
        self.assertFalse((self.recovery / "compensated").exists())
        self.assertFalse(any(self.dayz.rglob("*.restore-stage")))

        def fail_compensation(phase: str, _index: int) -> None:
            """Fail both publication and compensation to force recovery."""
            if phase in {"AFTER_PUBLISH", "BEFORE_COMPENSATE"}:
                raise OSError("synthetic compensation failure")
        # A compensation fault now demands recovery until it is reconciled
        second = self.service(RestoreStorage(fault_hook=fail_compensation))
        second_preview = second.preview("main", self.backup_id)
        with self.assertRaises(RestoreStorageError) as uncertain:
            self.apply(second, second_preview, "uncertain")
        self.assertTrue(uncertain.exception.recovery_required)
        self.assertTrue((self.recovery / "uncertain").exists())
        inspection = second.inspect_recovery()
        self.assertTrue(inspection["blocked"])
        recovered = self.service().inspect_recovery()
        self.assertFalse(recovered["blocked"])
        self.assertEqual(digest(self.target), before)

    def test_cancellation_before_publication_leaves_live_target_unchanged(self) -> None:
        """Cancellation before publication leaves the live target unchanged."""
        service = self.service()
        preview = service.preview("main", self.backup_id)
        # Capture live bytes before cancelling the run
        before = self.target.read_bytes()
        def cancel(phase: str, _percent: int) -> None:
            """Cancel the restore while writing the journal."""
            if phase == "WRITE_JOURNAL":
                raise OperationCancelled
        with self.assertRaises(OperationCancelled):
            service.apply(
                "main", self.backup_id, 3, 4, self.snapshot_manifest,
                preview["fingerprint"], "cancelled", cancel,
            )
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse(any((self.root / "operations" / "restore-journals").glob("*.json")))

    def test_custom_root_and_snapshot_tamper_are_verified_at_apply(self) -> None:
        """Custom roots work and a tampered snapshot fails at apply."""
        custom = self.root / "Custom Bäckup Root"
        custom.mkdir()
        # Point the manager at a custom backup root
        self.settings.value.custom_backup_root = str(custom)
        self.target.write_text("custom snapshot", encoding="utf-8")
        created = BackupService(
            self.profiles, self.settings, BackupStorage(),  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
            identifier=lambda: "custom",
        ).create("main", 3, 4, lambda _phase, _percent: None)
        backup_id = str(created["backup_id"])
        service = self.service()
        self.target.write_text("custom live", encoding="utf-8")
        preview = service.preview("main", backup_id)
        source = custom / f"{backup_id}.zip"
        # Tamper the archive payload after preview
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(source, "a") as archive:
                archive.writestr("payload/Config Files/serverDZ.cfg", "tampered backup")
        before = self.target.read_bytes()
        # The digest check rejects the tampered snapshot and preserves live bytes
        with self.assertRaises(BackupStorageError):
            service.apply(
                "main", backup_id, 3, 4, created["manifest_digest"],
                preview["fingerprint"], "tampered", lambda _phase, _percent: None,
            )
        self.assertEqual(self.target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
