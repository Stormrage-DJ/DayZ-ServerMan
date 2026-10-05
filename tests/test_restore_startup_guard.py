"""Startup recovery of an interrupted backup restore runs only inside the installation guard (QF-027, D14, QF-039)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_restores as restore_tests  # noqa: E402
from startup_guard_fixtures import GUARDED_RUN, GuardedStartupFixture  # noqa: E402
from dayz_serverman import composition  # noqa: E402
from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.log_activity import format_manager_record  # noqa: E402
from dayz_serverman.application.startup_recoveries import recover_interrupted_restores  # noqa: E402
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository  # noqa: E402
from dayz_serverman.repositories.restore_storage import RestoreStorage, RestoreStorageError  # noqa: E402

# Block reason of a restore recovery that the guard refused, and the reason that existed before
NOT_STOPPED = "Mutations are blocked by an interrupted backup restore while the server is not proven stopped."
UNRESOLVED = "Mutations are blocked by unresolved restore recovery."
# Block reason of a restore recovery without a DayZ server folder (QF-069)
NO_ROOT = "Backup restore recovery requires a configured DayZ root."


def fail_publication_and_compensation(phase: str, _index: int) -> None:
    """Fail after the first file is published and again in its compensation, so a journal stays behind."""
    if phase in {"AFTER_PUBLISH", "BEFORE_COMPENSATE"}:
        raise OSError("synthetic restore interruption")


class RestoreGuardFixture(GuardedStartupFixture):
    """Mixin for a TestCase: an interrupted restore below a disposable DayZ folder, and the guard doubles."""

    # The live tree with a snapshot backup of the restore tests
    service = restore_tests.RestoreTests.service
    apply = restore_tests.RestoreTests.apply

    def setUp(self) -> None:
        """Build the restore fixture, its journal folder and the guard doubles."""
        restore_tests.RestoreTests.setUp(self)
        self.addCleanup(self.temporary.cleanup)
        self.journals = RestoreJournalRepository(self.root / "operations" / "restore-journals")
        self.start_guard(self.root)
        self.roots = (self.dayz, self.root / "operations" / "restore-journals", self.recovery)

    def interrupt(self) -> bytes:
        """Leave a restore whose publication and compensation both failed; return the live bytes before it."""
        before = self.target.read_bytes()
        service = self.service(RestoreStorage(fault_hook=fail_publication_and_compensation))
        with self.assertRaises(RestoreStorageError) as raised:
            self.apply(service, service.preview("main", self.backup_id), "interrupted")
        self.assertTrue(raised.exception.recovery_required)
        self.assertNotEqual(self.target.read_bytes(), before)
        self.assertTrue(self.journals.records())
        return before

    def guarded_service(self):
        """Return a restore service whose installation guard uses the recording doubles."""
        return self.service(lifecycle=self.lifecycle, mutex=self.mutex)


class RestoreStartupRecoveryTests(RestoreGuardFixture, unittest.TestCase):
    """Restore recovery copies and renames files in the DayZ root, so it needs the mutex and a stopped server."""

    def recover(self) -> None:
        """Run the startup recovery; the restore service holds the guard over the doubles."""
        recover_interrupted_restores(self.journals, self.guarded_service(), self.operations)

    def test_stopped_server_recovers_as_before(self) -> None:
        """Under the mutex and with a stopped server the prior file is put back and the journal retired."""
        before = self.interrupt()
        self.recover()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.journals.records(), ())
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_each_state_that_is_not_stopped_writes_nothing_and_blocks(self) -> None:
        """Running, starting, stopping, outside the manager or unknown: no copy, no rename, one logged block."""
        before = self.interrupt()
        self.assert_each_refusal_writes_nothing(self.recover, self.roots, NOT_STOPPED)
        # The journal is kept, so a later start with a stopped server finishes the recovery
        self.recover()
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.journals.records(), ())

    def test_busy_installation_writes_nothing_and_blocks(self) -> None:
        """Another manager holds the installation: the state is not read and nothing is written."""
        before = self.interrupt()
        self.assert_busy_mutex_writes_nothing(self.recover, self.roots, NOT_STOPPED)
        self.recover()
        self.assertEqual(self.target.read_bytes(), before)

    def test_nothing_to_recover_does_nothing(self) -> None:
        """Without a journal the settings, the mutex and the server state are not touched."""
        before = self.tree(*self.roots)
        self.settings.load = lambda: self.fail("the settings must not be read")
        self.recover()
        self.assertEqual((self.tree(*self.roots), self.mutex.events), (before, []))
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))

    def test_earlier_block_reason_is_kept(self) -> None:
        """No DayZ folder blocks with its own reason (QF-069); an unprovable prior state keeps the earlier one."""
        self.interrupt()
        before = self.tree(*self.roots)
        self.settings.value.dayz_root = None
        self.recover()
        self.assertEqual((self.operations.recovery_block, self.mutex.events), (NO_ROOT, []))
        self.assertEqual(self.tree(*self.roots), before)
        self.settings.value.dayz_root = str(self.dayz)
        # The live file changed after the interruption: the recovery runs in the guard and cannot prove it
        self.target.write_bytes(b"changed by hand")
        self.recover()
        self.assertEqual(self.operations.recovery_block, UNRESOLVED)
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_new_reason_has_an_operator_sentence_in_manager_activity(self) -> None:
        """The logged reason is worded by the catalogue: what happened and what to do."""
        sentence = wording.block_reason_text(NOT_STOPPED)
        self.assertEqual(sentence, (
            "A backup restore was interrupted and must be finished. This is possible only while the server is "
            "stopped and no other DayZ-ServerMan uses this DayZ installation. "
            "Stop the server, then open Backups again or restart DayZ-ServerMan."))
        self.assertFalse(wording.leaks_identifier(sentence))
        self.assertNotEqual(wording.block_reason_text(UNRESOLVED), sentence)
        line = format_manager_record({"event": "operation_lane.recovery_block", "level": "ERROR",
                                      "occurred_at": "2026-10-04T10:00:00Z", "fields": {"reason": NOT_STOPPED}})
        self.assertTrue(line.endswith(f"Changes are now blocked. {sentence}"), line)

    def test_composition_recovers_only_through_the_guarded_function(self) -> None:
        """The composition root runs the restore recovery through the guard built on the lifecycle and mutex."""
        source = Path(composition.__file__).read_text(encoding="utf-8")
        self.assertNotIn("restores.inspect_recovery()", source)
        self.assertIn("recover_interrupted_restores(restore_journals, restores, operations)", source)
        # The service gets the lifecycle and the mutex of its guard
        self.assertIn("paths.backup_recovery, lifecycle, mutex,", source)
        self.assertLess(source.index("= build_lifecycle("), source.index("restores = RestoreService("))


if __name__ == "__main__":
    unittest.main()
