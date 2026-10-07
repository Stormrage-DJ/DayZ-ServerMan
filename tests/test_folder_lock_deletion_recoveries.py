"""Profile deletion and the mod publication startup recovery under a held A13 reader (3.3)."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applied_gate_fixtures import CountingLifecycle  # noqa: E402
from folder_lock_fixtures import body_and_gate_free, held_reader, tree_hashes, writer_for  # noqa: E402
from profile_fixtures import create_profile_paths, profile_payload  # noqa: E402
import test_mod_publication_recovery as recovery_tests  # noqa: E402
from test_mod_publication_inventory import PublicationFixture  # noqa: E402
from test_mod_publication_startup import NOT_STOPPED  # noqa: E402
from test_publication_start_guard import _Mutex  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile  # noqa: E402
from dayz_serverman.application import folder_writer_scope, mod_publication_startup  # noqa: E402
from dayz_serverman.application.installation_guard import InstallationGuard  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.models import SettingsInput  # noqa: E402
from dayz_serverman.repositories.mod_publication_journal import PublicationJournalRepository  # noqa: E402

# Terminal operation states
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class ProfileDeletionUnderReaderTests(unittest.TestCase):
    """DELETE_PROFILE takes the writer side before its first change; ProfileCoordinator maps the refusal."""

    def setUp(self) -> None:
        """Compose an owner with a writer side and save a profile with a generated folder and world storage."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_delete_a13_")
        self.addCleanup(temporary.cleanup)
        self.manager = Path(temporary.name) / "Manager"
        self.dayz = Path(temporary.name) / "DayZ Root"
        create_profile_paths(self.dayz)
        self.lock_path = self.manager / "data" / "server-folders.lock"
        self.lock_path.parent.mkdir(parents=True)
        FolderLockFile.create(self.lock_path).close()
        writer = writer_for(self.lock_path)
        self.addCleanup(writer.close)
        self.composition = build_composition(self.manager, folder_writer=writer)
        self.addCleanup(self.composition.operations.shutdown, 2)
        self.composition.settings.save(SettingsInput(
            dayz_root=str(self.dayz), dayz_executable=str(self.dayz / "Bin" / "DayZ Server_x64.exe")), None)
        self.generated = self.dayz / "serverman" / "livonia-main"
        (self.generated / "profile").mkdir(parents=True)
        (self.generated / "serverDZ.cfg").write_text("instanceId = 17;\n", encoding="utf-8")
        self.storage = self.dayz / "mpmissions" / "dayzOffline.enoch" / "storage_17"
        self.storage.mkdir(parents=True)
        (self.storage / "players.db").write_bytes(b"world")
        saved = self.run_method("save_profile", {"profile": profile_payload(
            server_config=r"serverman\livonia-main\serverDZ.cfg",
            runtime_profile=r"serverman\livonia-main\profile"), "expected_revision": None})
        self.assertEqual(saved.state.value, "SUCCEEDED")

    def run_method(self, method: str, parameters: dict):
        """Dispatch one lane method and return its terminal record."""
        answer = self.composition.bridge.dispatch(
            {"contract_version": 1, "request_id": "a13", "method": method, "parameters": parameters})
        operation_id = answer["value"]["operation_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record = self.composition.operations.get(operation_id)
            if record.state.value in TERMINAL:
                return record
            time.sleep(0.01)
        self.fail("operation did not finish")

    def test_refusal_changes_nothing_and_is_control_conflict(self) -> None:
        """The profile, its schedule and preferences, the generated folder and the world all stay."""
        roots = (self.dayz, self.manager / "data" / "profiles")
        before = tree_hashes(*roots)
        with held_reader(self.lock_path), patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", 0.3):
            record = self.run_method("delete_profile", {"profile_id": "livonia-main", "expected_revision": 0})
        self.assertEqual(record.state.value, "FAILED")
        self.assertEqual((record.terminal_error.code, record.terminal_error.retryable), ("CONTROL_CONFLICT", True))
        self.assertEqual(tree_hashes(*roots), before)
        self.assertTrue(body_and_gate_free(self.lock_path))
        # Without the reader the deletion runs and releases the writer side
        record = self.run_method("delete_profile", {"profile_id": "livonia-main", "expected_revision": 0})
        self.assertEqual(record.state.value, "SUCCEEDED")
        self.assertFalse(self.generated.exists())
        self.assertTrue(body_and_gate_free(self.lock_path))


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class PublicationStartupUnderReaderTests(PublicationFixture):
    """The startup recovery of a mod publication holds the writer side; under a reader it blocks as today."""

    # The interrupted publication of the recovery tests: alpha is swapped, beta is not
    _save_new = staticmethod(recovery_tests.PublicationRecoveryTests._save_new)
    interrupt = recovery_tests.PublicationRecoveryTests._interrupt_after_first

    def setUp(self) -> None:
        """Seed live mod folders, a journal repository, the lane, the guard doubles and a lock file."""
        super().setUp()
        self.dayz = self.base / "DayZ"
        for relative, content in (("mods/alpha", b"old-a"), ("mods/beta", b"old-b")):
            (self.dayz / relative).mkdir(parents=True)
            (self.dayz / relative / "old.pbo").write_bytes(content)
        (self.dayz / "keys").mkdir()
        self.repository = PublicationJournalRepository(self.base / "journals")
        self.operations = OperationManager(OperationStore(self.base / "operations"))
        self.addCleanup(self.operations.shutdown, 2)
        self.mutex = _Mutex()
        self.settings = SimpleNamespace(load=lambda: SimpleNamespace(dayz_root=str(self.dayz)))
        self.lock_path = self.base / "server-folders.lock"
        FolderLockFile.create(self.lock_path).close()
        self.writer = writer_for(self.lock_path)
        self.addCleanup(self.writer.close)
        self.guard = InstallationGuard(CountingLifecycle(), self.mutex).with_folder_writer(self.writer)

    def test_reader_keeps_the_interrupted_set_for_a_later_start(self) -> None:
        """Today's block reason; nothing changes; without the reader the set rolls back."""
        self.interrupt()
        before = tree_hashes(self.dayz, self.base / "journals")
        with held_reader(self.lock_path), patch.object(mod_publication_startup, "STARTUP_RECOVERY_WAIT_SECONDS", 0.3):
            mod_publication_startup.recover_interrupted_publications(
                self.repository, self.settings, self.operations, self.guard)
        self.assertEqual(self.operations.recovery_block, NOT_STOPPED)
        self.assertEqual(tree_hashes(self.dayz, self.base / "journals"), before)
        self.assertFalse(self.mutex.held)
        self.assertTrue(body_and_gate_free(self.lock_path))
        self.operations.clear_recovery_block()
        mod_publication_startup.recover_interrupted_publications(
            self.repository, self.settings, self.operations, self.guard)
        self.assertEqual((self.dayz / "mods/alpha/old.pbo").read_bytes(), b"old-a")
        self.assertEqual(list(self.repository.records()), [])


if __name__ == "__main__":
    unittest.main()
