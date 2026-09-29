"""Restore profile tests for runtime files, context matching, and compensation."""
from __future__ import annotations

import sys
import tempfile
import unittest
import contextlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dayz_serverman.application.backups import BackupService  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled  # noqa: E402
from dayz_serverman.application.restores import RestoreService  # noqa: E402
from dayz_serverman.domain.backups import entry_path_key  # noqa: E402
from dayz_serverman.repositories.backups import BackupStorage, BackupStorageError  # noqa: E402
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorage, RestoreStorageError  # noqa: E402
from tests.test_backups import FakeProfiles, FakeSettings, record  # noqa: E402
from tests.test_restores import FakeLifecycle, FakeMutex  # noqa: E402


class RestoreProfileV2Tests(unittest.TestCase):
    """Runtime profile restore contracts for context, faults, and residue cleanup."""
    def setUp(self) -> None:
        """Create the live DayZ tree, backup destination, and fake ports."""
        self.temporary = tempfile.TemporaryDirectory(prefix="serverman_restore_v2_")
        self.root = Path(self.temporary.name)
        self.dayz = self.root / "DayZ"
        self.target = self.dayz / "Config Files" / "serverDZ.cfg"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("snapshot config", encoding="utf-8")
        (self.dayz / "profiles" / "main").mkdir(parents=True)
        self.backups = self.root / "manager" / "backups"
        self.backups.mkdir(parents=True)
        self.recovery = self.backups / "recovery"
        self.recovery.mkdir()
        self.profiles = FakeProfiles(record(False))
        self.settings = FakeSettings(self.dayz, self.backups)

    def tearDown(self) -> None:
        """Remove the temporary tree."""
        self.temporary.cleanup()

    def backup(self, identifier: str) -> dict[str, object]:
        """Create a deterministic backup fixture for the active profile."""
        return BackupService(
            self.profiles, self.settings, BackupStorage(),  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
            identifier=lambda: identifier,
        ).create("main", 3, 4, lambda *_: None)

    def service(self, storage: RestoreStorage | None = None) -> RestoreService:
        """Build a restore service with optional storage overrides."""
        return RestoreService(
            self.profiles, self.settings, BackupStorage(), storage or RestoreStorage(),
            RestoreJournalRepository(self.root / "journals"), self.recovery,
            FakeLifecycle(), FakeMutex(),
        )  # type: ignore[arg-type]

    def test_nested_runtime_files_restore_as_replace_and_create(self) -> None:
        """Nested runtime files restore as create and replace operations."""
        runtime = self.dayz / "profiles" / "main"
        runtime_file = runtime / "runtime.cfg"
        nested = runtime / "nested" / "state.json"
        nested.parent.mkdir()
        runtime_file.write_text("snapshot runtime", encoding="utf-8")
        nested.write_text("snapshot nested", encoding="utf-8")
        created = self.backup("runtime-files")
        runtime_file.write_text("live runtime", encoding="utf-8")
        nested.unlink()
        nested.parent.rmdir()
        # Preview plans a create and a replace for the runtime targets
        service = self.service()
        preview = service.preview("main", created["backup_id"])
        targets = [item for item in preview["targets"] if item["target_kind"] == "RUNTIME_PROFILE"]
        self.assertEqual([item["action"] for item in targets], ["CREATE", "REPLACE"])
        self.assertTrue(all(item["target_relative"].startswith("profiles/main/") for item in targets))
        # Apply restores the snapshot content byte for byte
        service.apply(
            "main", created["backup_id"], 3, 4, created["manifest_digest"],
            preview["fingerprint"], "runtime-restore", lambda *_: None,
        )
        self.assertEqual(runtime_file.read_text(encoding="utf-8"), "snapshot runtime")
        self.assertEqual(nested.read_text(encoding="utf-8"), "snapshot nested")

    def test_profile_identity_and_semantic_digest_match_at_preview_and_apply(self) -> None:
        """Profile context mismatches fail preview or apply with a typed error."""
        created = self.backup("profile-context")
        original = self.profiles.record
        self.profiles.record = replace(original, revision=4)
        # A changed profile revision blocks preview
        with self.assertRaises(BackupStorageError) as revision:
            self.service().preview("main", created["backup_id"])
        self.assertEqual(revision.exception.code, "PROFILE_CONTEXT_MISMATCH")
        self.profiles.record = original
        (self.dayz / "profiles" / "other").mkdir()
        self.profiles.record = replace(
            original, values=replace(original.values, runtime_profile="profiles\\other"),
        )
        # A different runtime profile identity blocks preview
        with self.assertRaises(BackupStorageError) as identity:
            self.service().preview("main", created["backup_id"])
        self.assertEqual(identity.exception.code, "PROFILE_CONTEXT_MISMATCH")
        self.profiles.record = original
        preview = self.service().preview("main", created["backup_id"])
        self.profiles.record = replace(
            original, values=replace(original.values, extra_arguments=("-doLogs",)),
        )
        # A changed semantic digest blocks apply
        with self.assertRaises(BackupStorageError) as semantic:
            self.service().apply(
                "main", created["backup_id"], 3, 4, created["manifest_digest"],
                preview["fingerprint"], "context-mismatch", lambda *_: None,
            )
        self.assertEqual(semantic.exception.code, "PROFILE_CONTEXT_MISMATCH")

    def test_runtime_group_fault_compensates_and_cleans_residue(self) -> None:
        """A fault while creating ancestors compensates and removes all residue."""
        runtime = self.dayz / "profiles" / "main" / "new" / "deep" / "state.json"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("snapshot", encoding="utf-8")
        # Snapshot a deep runtime file, then remove it from the live tree
        created = self.backup("runtime-fault")
        runtime.unlink()
        runtime.parent.rmdir()
        runtime.parent.parent.rmdir()

        def fault(phase: str, index: int) -> None:
            """Fail while creating the second runtime ancestor."""
            if phase == "AFTER_CREATE_ANCESTOR" and index == 1:
                raise OSError("synthetic runtime ancestor fault")

        service = self.service(RestoreStorage(fault_hook=fault))
        preview = service.preview("main", created["backup_id"])
        # The compensation restores prior files and clears every residue
        with self.assertRaisesRegex(RestoreStorageError, "prior files were restored"):
            service.apply(
                "main", created["backup_id"], 3, 4, created["manifest_digest"],
                preview["fingerprint"], "runtime-fault", lambda *_: None,
            )
        self.assertFalse(runtime.exists())
        self.assertFalse(runtime.parent.exists())
        self.assertFalse(runtime.parent.parent.exists())
        self.assertFalse(any(self.dayz.rglob("*.restore-stage")))
        self.assertFalse((self.recovery / "runtime-fault").exists())

    def test_unsupported_payload_fails_before_mutex_lifecycle_or_mapping(self) -> None:
        """Unsupported snapshot payloads fail before the mutex or lifecycle are used."""
        extra = self.dayz / "arbitrary.cfg"
        extra.write_text("unsupported", encoding="utf-8")
        # Handcraft a snapshot whose second entry is unsupported
        storage = BackupStorage()
        sources = tuple(sorted((
            storage.source(self.dayz, "Config Files\\serverDZ.cfg"),
            storage.source(self.dayz, "arbitrary.cfg"),
        ), key=lambda item: entry_path_key(item.entry_path)))
        manifest = storage.create(
            self.backups, "unsupported-content", "main", 3, 4,
            "2026-09-25T18:00:00.000Z", self.profiles.record.semantic_digest,
            "profiles\\main", sources, lambda *_: None,
        )

        class ForbiddenMutex:
            """Mutex port that fails the test if the guard is entered."""
            @contextlib.contextmanager
            def guard(self, _root: str):
                """Fail if the restore ever enters the mutex."""
                raise AssertionError("mutex must not be entered")
                yield

        class ForbiddenLifecycle:
            """Lifecycle port that fails the test if it is queried."""
            def status(self):
                """Fail if the restore ever queries the lifecycle."""
                raise AssertionError("lifecycle must not be queried")

        # The forbidden ports fail the test if preview touches them
        service = RestoreService(
            self.profiles, self.settings, storage, RestoreStorage(),
            RestoreJournalRepository(self.root / "unsupported-journals"), self.recovery,
            ForbiddenLifecycle(), ForbiddenMutex(),
        )  # type: ignore[arg-type]
        with self.assertRaises(BackupStorageError) as raised:
            service.preview("main", manifest.backup_id)
        self.assertEqual(raised.exception.code, "UNSUPPORTED_SNAPSHOT_CONTENT")

    def test_missing_ancestor_staging_cancellation_leaves_no_residue(self) -> None:
        """Cancellation during ancestor staging leaves no residue behind."""
        runtime = self.dayz / "profiles" / "main" / "cancel" / "deep" / "state.json"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("snapshot", encoding="utf-8")
        created = self.backup("ancestor-cancel")
        runtime.unlink()
        runtime.parent.rmdir()
        runtime.parent.parent.rmdir()

        staged = 0
        def checkpoint(phase: str, _percent: int) -> None:
            """Cancel the second staging checkpoint for the runtime group."""
            nonlocal staged
            if phase == "STAGE_TARGETS":
                staged += 1
            if phase == "STAGE_TARGETS" and staged == 2:
                raise OperationCancelled

        # Preview plans the missing ancestors, then cancel mid-staging
        preview = self.service().preview("main", created["backup_id"])
        with self.assertRaises(OperationCancelled):
            self.service().apply(
                "main", created["backup_id"], 3, 4, created["manifest_digest"],
                preview["fingerprint"], "ancestor-cancel", checkpoint,
            )
        self.assertFalse(runtime.parent.exists())
        self.assertFalse(any((self.dayz / "profiles" / "main").glob(".serverman-*-stage")))
        self.assertFalse((self.recovery / "ancestor-cancel").exists())

    def test_startup_reconciles_interrupted_runtime_group(self) -> None:
        """Startup reconciliation resolves an interrupted runtime group."""
        runtime = self.dayz / "profiles" / "main" / "nested" / "deep" / "state.json"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("snapshot", encoding="utf-8")
        created = self.backup("runtime-startup")
        runtime.unlink()
        runtime.parent.rmdir()
        runtime.parent.parent.rmdir()

        def interrupt(phase: str, index: int) -> None:
            """Interrupt publication and compensation to force reconciliation."""
            if (phase == "AFTER_CREATE_ANCESTOR" and index == 1) or phase == "BEFORE_COMPENSATE":
                raise OSError("synthetic interrupted runtime compensation")

        storage = RestoreStorage(fault_hook=interrupt)
        service = self.service(storage)
        preview = service.preview("main", created["backup_id"])
        with self.assertRaises(RestoreStorageError) as raised:
            service.apply(
                "main", created["backup_id"], 3, 4, created["manifest_digest"],
                preview["fingerprint"], "runtime-startup", lambda *_: None,
            )
        self.assertTrue(raised.exception.recovery_required)
        # A fresh inspection reconciles the interrupted group at startup
        journals = RestoreJournalRepository(self.root / "journals")
        result = RestoreStorage().inspect(journals, self.dayz, self.recovery)
        self.assertFalse(result["blocked"])
        self.assertFalse(runtime.exists())
        self.assertFalse(runtime.parent.exists())
        self.assertFalse(runtime.parent.parent.exists())
        self.assertTrue(journals.retired_path_for("runtime-startup").is_file())


if __name__ == "__main__":
    unittest.main()
