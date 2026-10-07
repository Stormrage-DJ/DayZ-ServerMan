"""Apply and restart under A13 (QF-2): the writer side is taken before the stop and handed to the publication."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from folder_lock_fixtures import body_and_gate_free, held_reader, tree_hashes, writer_for  # noqa: E402
import test_mod_restart_coordinator as restart_tests  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile, FolderReader  # noqa: E402
from dayz_serverman.application import folder_writer_scope  # noqa: E402
from dayz_serverman.application.backups import BackupStorageError  # noqa: E402
from dayz_serverman.application.mod_publication import ModPublicationError  # noqa: E402
from dayz_serverman.application.mod_restart_coordinator import ModRestartCoordinator  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.domain.lifecycle import LifecycleFailure  # noqa: E402

# The request of every run; the fake services ignore its values
REQUEST = {
    "profile_id": "main", "expected_profile_revision": 1, "expected_semantic_profile_digest": "a" * 64,
    "expected_settings_revision": 2, "update_operation_id": "update-1", "publication_fingerprint": "b" * 64,
}


class _CountingWriter:
    """Writer side that counts its acquisitions."""

    def __init__(self, writer) -> None:
        """Wrap a real writer side."""
        self.writer = writer
        self.count = 0

    @contextmanager
    def exclusive(self, bound_seconds: float):
        """Count, then hold the real writer side."""
        self.count += 1
        with self.writer.exclusive(bound_seconds):
            yield


class _Services(restart_tests._Services):
    """The coordinator test fakes, with a server state and a publication that accepts the handed scope."""

    def __init__(self, lock_path: Path) -> None:
        """Start with a running server and no probe result."""
        super().__init__()
        self.lock_path = lock_path
        self.state = "RUNNING_MANAGED"
        self.handed: list[object] = []
        self.reads: dict[str, bool] = {}

    def stop(self, settings_revision: int, *, before_change=None) -> None:
        """Log the stop; the server is stopped unless the stop fails."""
        super().stop(settings_revision)
        self.state = "STOPPED"

    def publish(self, request, fingerprint, context, writer_scope=None) -> dict[str, object]:
        """Run the fake phases; release the scope after the live changes, as apply_publication does."""
        self.handed.append(writer_scope)
        services = self

        class _Context:
            """Forward the phases; probe a reader in the pre-start check and the start."""

            def checkpoint(self, phase: str, percent: int) -> None:
                """Release before VERIFY_BEFORE_START, then try one reader call in each start phase."""
                if phase == "VERIFY_BEFORE_START" and writer_scope is not None:
                    writer_scope.release()
                if phase in {"VERIFY_BEFORE_START", "START_SERVER"}:
                    reader = FolderReader(FolderLockFile.create(services.lock_path))
                    try:
                        with reader.shared(0.2):
                            services.reads[phase] = True
                    finally:
                        reader.close()
                context.checkpoint(phase, percent)
        return super().publish(request, fingerprint, _Context())


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class RestartApplyUnderReaderTests(unittest.TestCase):
    """The order of QF-2: preflight, writer side, stop, backup, publication with the scope, release."""

    def setUp(self) -> None:
        """Create the lane, a lock file, the counted writer side and the coordinator over the fakes."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_restart_a13_")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.lock_path = self.base / "server-folders.lock"
        FolderLockFile.create(self.lock_path).close()
        writer = writer_for(self.lock_path)
        self.addCleanup(writer.close)
        self.writer = _CountingWriter(writer)
        self.manager = OperationManager(OperationStore(self.base / "operations"))
        self.addCleanup(self.manager.shutdown, 3)
        self.services = _Services(self.lock_path)
        self.services.manager = self.manager
        self.coordinator = ModRestartCoordinator(
            self.services, self.services, self.services, self.manager, folder_writer=self.writer)
        (self.base / "DayZ" / "@Mod").mkdir(parents=True)
        (self.base / "DayZ" / "@Mod" / "mod.cpp").write_bytes(b"old")

    def run_restart(self, backup: bool = False):
        """Submit the restart and return the terminal operation record."""
        accepted = self.coordinator.apply({**REQUEST, "backup_after_stop": backup})
        self.services.operation_id = accepted["operation_id"]
        self.services.submitted.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            record = self.manager.get(accepted["operation_id"])
            if record.state.value in {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}:
                return record
            time.sleep(0.01)
        self.fail("operation did not finish")

    def test_held_reader_refuses_after_about_5_s_before_the_stop(self) -> None:
        """CONTROL_CONFLICT at preflight; the stop is never called; the server keeps running; nothing changed."""
        before = tree_hashes(self.base / "DayZ")
        with held_reader(self.lock_path):
            started = time.monotonic()
            record = self.run_restart(backup=True)
            waited = time.monotonic() - started
        self.assertEqual(record.state.value, "FAILED")
        self.assertEqual(record.terminal_error.code, "CONTROL_CONFLICT")
        self.assertEqual(record.last_working_phase, "preflight")
        self.assertGreaterEqual(waited, 4.5)
        self.assertEqual(self.services.calls, ["confirm"])
        self.assertEqual(self.services.state, "RUNNING_MANAGED")
        self.assertEqual(tree_hashes(self.base / "DayZ"), before)
        self.assertEqual(self.writer.count, 1)
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_one_acquisition_per_run_handed_to_the_publication_and_free_in_the_start(self) -> None:
        """One writer side per run; the publication gets the scope; readers run in the pre-start check and start."""
        record = self.run_restart(backup=True)
        self.assertEqual(record.state.value, "SUCCEEDED")
        self.assertEqual(self.writer.count, 1)
        self.assertEqual(len(self.services.handed), 1)
        self.assertIsNotNone(self.services.handed[0])
        self.assertEqual(self.services.reads, {"VERIFY_BEFORE_START": True, "START_SERVER": True})
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_nothing_to_apply_takes_no_writer_side(self) -> None:
        """A plan that writes nothing returns before the lock."""
        self.services.writes = False
        self.assertEqual(self.run_restart().state.value, "SUCCEEDED")
        self.assertEqual(self.writer.count, 0)

    def test_scope_is_released_after_each_early_end(self) -> None:
        """A stop failure, a backup failure, a stale preview in publish and a cancellation all release it."""
        cases = {
            "stop failure": ({"stop": LifecycleFailure("CONTROL_CONFLICT", "busy")}, None),
            "backup failure": ({"backup": BackupStorageError("BACKUP_FAILED", "failed")}, None),
            "stale preview": ({"publish": ModPublicationError("PUBLICATION_PREVIEW_STALE", "changed")}, None),
            "cancellation": ({}, "BACKUP_STAGE"),
        }
        for name, (failures, cancel_at) in cases.items():
            with self.subTest(case=name):
                self.services.fail, self.services.cancel_at = failures, cancel_at
                record = self.run_restart(backup=True)
                self.assertNotEqual(record.state.value, "SUCCEEDED")
                self.assertTrue(body_and_gate_free(self.lock_path))
        self.assertEqual(self.writer.count, len(cases))

    def test_owner_wait_is_the_5_s_bound(self) -> None:
        """The scope reads the owner wait when it is taken (OD7: about 5 s)."""
        self.assertEqual(folder_writer_scope.OWNER_WRITER_WAIT_SECONDS, 5.0)
        with patch.object(folder_writer_scope, "OWNER_WRITER_WAIT_SECONDS", 0.2), held_reader(self.lock_path):
            started = time.monotonic()
            self.assertEqual(self.run_restart().terminal_error.code, "CONTROL_CONFLICT")
            self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
