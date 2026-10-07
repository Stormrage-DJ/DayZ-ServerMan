"""Profile restore under A13 (R-5): the writer side is taken before staging, so a refusal changes nothing."""

from __future__ import annotations

import os
import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from folder_lock_fixtures import body_and_gate_free, held_reader, tree_hashes, writer_for  # noqa: E402
import test_profile_restore_service as direct_tests  # noqa: E402
from startup_guard_fixtures import GuardedStartupFixture  # noqa: E402
from test_profile_restore_startup_guard import NOT_STOPPED, crash_after_profile_publication  # noqa: E402
from dayz_serverman.adapters.windows.server_folder_lock import FolderLockFile  # noqa: E402
from dayz_serverman.application import startup_recoveries  # noqa: E402
from dayz_serverman.application.installation_guard import InstallationGuard  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.models import OperationCancelled  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.profile_restore_coordinator import ProfileRestoreCoordinator  # noqa: E402
from dayz_serverman.application.profile_restores import ProfileRestoreService  # noqa: E402

# Terminal operation states
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERY_REQUIRED"}


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


@unittest.skipUnless(os.name == "nt", "LockFileEx is a Windows call")
class ProfileRestoreUnderReaderTests(GuardedStartupFixture, unittest.TestCase):
    """RESTORE_PROFILE_FROM_BACKUP on a DayZ root without serverman, with and without a held reader."""

    parameters = direct_tests.DirectRestoreTests.parameters

    def setUp(self) -> None:
        """Build the direct-restore fixture, a lock file, and the service with a counted writer side."""
        direct_tests.DirectRestoreTests.setUp(self)
        self.lock_path = self.root / "server-folders.lock"
        FolderLockFile.create(self.lock_path).close()
        writer = writer_for(self.lock_path)
        self.addCleanup(writer.close)
        self.writer = _CountingWriter(writer)
        base = self.service
        self.service = ProfileRestoreService(
            base.profiles, base.settings, base.backups, base.storage, base.lifecycle, base.mutex,
            base.udp_inventory, folder_writer=self.writer,
        )
        self.lane = OperationManager(OperationStore(self.root / "operations"))
        self.addCleanup(self.lane.shutdown, 2)
        self.roots = (self.dayz, self.repository.root, self.root / "journals", self.root / "recovery")

    def run_operation(self, parameters):
        """Submit the restore through its coordinator and return the terminal record."""
        accepted = ProfileRestoreCoordinator(self.service, self.lane).apply(parameters)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            record = self.lane.get(accepted["operation_id"])
            if record.state.value in TERMINAL:
                return record
            time.sleep(0.02)
        self.fail("operation did not finish")

    def test_refusal_after_about_5_s_leaves_no_serverman_folder_and_no_journal(self) -> None:
        """CONTROL_CONFLICT with last phase null; no serverman folder, no journal; the trees are unchanged."""
        self.assertFalse((self.dayz / "serverman").exists())
        parameters = self.parameters()
        before = tree_hashes(*self.roots)
        with held_reader(self.lock_path):
            started = time.monotonic()
            record = self.run_operation(parameters)
            waited = time.monotonic() - started
        self.assertEqual(record.state.value, "FAILED")
        self.assertEqual(record.terminal_error.code, "CONTROL_CONFLICT")
        self.assertIsNone(record.last_working_phase)
        self.assertGreaterEqual(waited, 4.5)
        self.assertFalse((self.dayz / "serverman").exists())
        self.assertEqual(list((self.root / "journals").glob("*")) if (self.root / "journals").exists() else [], [])
        self.assertEqual(tree_hashes(*self.roots), before)
        self.assertEqual(self.writer.count, 1)
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_one_acquisition_per_run_and_a_reader_may_read_afterwards(self) -> None:
        """A run without a reader takes the writer side exactly once and releases it."""
        record = self.run_operation(self.parameters())
        self.assertEqual(record.state.value, "SUCCEEDED")
        self.assertEqual(self.writer.count, 1)
        self.assertTrue(body_and_gate_free(self.lock_path))

    def test_release_after_a_staging_failure_and_a_cancellation(self) -> None:
        """The writer side ends with the run on both failure paths."""
        def fail_staging(phase: str) -> None:
            """Fail when the staging starts."""
            if phase == "PREPARING":
                raise OSError("synthetic staging failure")
        self.storage.hook = fail_staging
        with self.assertRaises(OSError):
            self.service.apply(self.parameters(), "staging-failure", lambda *_: None)
        self.assertTrue(body_and_gate_free(self.lock_path))
        self.storage.hook = lambda _phase: None

        def cancel(phase: str, _percent: int) -> None:
            """Cancel at the first staging checkpoint."""
            if phase == "PREPARING":
                raise OperationCancelled("synthetic cancellation")
        with self.assertRaises(OperationCancelled):
            self.service.apply(self.parameters(), "cancelled", cancel)
        self.assertTrue(body_and_gate_free(self.lock_path))
        self.assertEqual(self.writer.count, 2)

    def test_startup_recovery_under_a_reader_ends_in_todays_block(self) -> None:
        """An interrupted profile restore stays for a later start; the block reason is today's."""
        parameters = self.parameters()
        self.storage.hook = crash_after_profile_publication
        with self.assertRaises(KeyboardInterrupt):
            self.service.apply(parameters, "crashed", lambda *_: None)
        self.storage.hook = lambda _phase: None
        self.start_guard(self.root)
        guard = InstallationGuard(self.lifecycle, self.mutex, self.writer.writer)
        before = tree_hashes(*self.roots)
        with held_reader(self.lock_path), patch.object(startup_recoveries, "STARTUP_RECOVERY_WAIT_SECONDS", 0.3):
            startup_recoveries.recover_interrupted_profile_restores(self.storage, self.settings, self.operations, guard)
        self.assertEqual(self.operations.recovery_block, NOT_STOPPED)
        self.assertEqual(tree_hashes(*self.roots), before)
        self.assertFalse(self.mutex.held)
        # Without the reader the same recovery undoes the restore
        startup_recoveries.recover_interrupted_profile_restores(self.storage, self.settings, self.operations, guard)
        self.assertFalse((self.dayz / "serverman" / "restored").exists())


if __name__ == "__main__":
    unittest.main()
