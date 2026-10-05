"""Startup recovery of an interrupted direct profile restore reads the state under the mutex (QF-027, D14)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

import test_profile_restore_service as direct_tests  # noqa: E402
from startup_guard_fixtures import GUARDED_RUN, GuardedStartupFixture  # noqa: E402
from dayz_serverman import profile_restore_composition  # noqa: E402
from dayz_serverman.application import activity_wording as wording  # noqa: E402
from dayz_serverman.application.startup_recoveries import recover_interrupted_profile_restores  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402
from dayz_serverman.repositories.profiles import ProfileNotFound  # noqa: E402

# Block reason of a direct restore recovery that the guard refused, and the reason that existed before
NOT_STOPPED = "Mutations are blocked by an interrupted direct profile restore while the server is not proven stopped."
ATTENTION = "Direct profile restore recovery requires attention."
# Block reasons of the two causes that a stop and restart does not resolve (QF-075)
NO_ROOT = "Direct profile restore recovery requires a configured DayZ root."
UNREADABLE = "Direct profile restore journals could not be read."


def crash_after_profile_publication(phase: str) -> None:
    """End the restore as a manager crash would, after the profile record is published."""
    if phase == "PROFILE_PUBLISHED":
        raise KeyboardInterrupt


class ProfileRestoreStartupRecoveryTests(GuardedStartupFixture, unittest.TestCase):
    """Direct restore recovery writes into the DayZ root, so it needs the mutex and a stopped server."""

    parameters = direct_tests.DirectRestoreTests.parameters

    def setUp(self) -> None:
        """Build the direct restore fixture with a real archive, and the guard doubles."""
        direct_tests.DirectRestoreTests.setUp(self)
        self.start_guard(self.root)
        self.roots = (self.dayz, self.repository.root, self.root / "journals", self.root / "recovery")

    def interrupt(self) -> Path:
        """Leave a direct restore that ended after its profile was published; return its profile folder."""
        self.storage.hook = crash_after_profile_publication
        with self.assertRaises(KeyboardInterrupt):
            self.service.apply(self.parameters(), "crashed", lambda *_: None)
        self.storage.hook = lambda _phase: None
        self.assertEqual(self.profiles.read("restored").revision, 0)
        return self.dayz / "serverman" / "restored"

    def recover(self) -> None:
        """Run the startup recovery with the guard over the doubles."""
        recover_interrupted_profile_restores(self.storage, self.settings, self.operations, self.guard)

    def assert_recovered(self, folder: Path) -> None:
        """The published profile record and its folder are gone, and no journal is unfinished."""
        with self.assertRaises(ProfileNotFound):
            self.profiles.read("restored")
        self.assertFalse(folder.exists())
        self.assertTrue(all(record["phase"] == "ROLLED_BACK" for record in self.journals.records()))

    def test_stopped_server_recovers_as_before(self) -> None:
        """Under the mutex and with a stopped server the interrupted restore is undone."""
        folder = self.interrupt()
        self.assertTrue(folder.exists())
        self.recover()
        self.assert_recovered(folder)
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))
        # The state is read after the mutex is taken, not before
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_each_state_that_is_not_stopped_writes_nothing_and_blocks(self) -> None:
        """Running, starting, stopping, outside the manager or unknown: nothing changes, one logged block."""
        folder = self.interrupt()
        self.assert_each_refusal_writes_nothing(self.recover, self.roots, NOT_STOPPED)
        # The journal is kept, so a later start with a stopped server finishes the recovery
        self.recover()
        self.assert_recovered(folder)

    def test_busy_installation_writes_nothing_and_blocks(self) -> None:
        """Another manager holds the installation: the state is not read and nothing is written."""
        folder = self.interrupt()
        self.assert_busy_mutex_writes_nothing(self.recover, self.roots, NOT_STOPPED)
        self.recover()
        self.assert_recovered(folder)

    def test_nothing_to_recover_does_nothing(self) -> None:
        """Without an unfinished journal the settings, the mutex and the server state are not touched."""
        before = self.tree(*self.roots)
        self.settings.load = lambda: self.fail("the settings must not be read")
        self.recover()
        self.assertEqual((self.tree(*self.roots), self.mutex.events), (before, []))
        self.assertEqual((self.operations.recovery_block, self.logged_blocks()), (None, []))

    def test_each_cause_blocks_with_its_own_reason(self) -> None:
        """QF-075: no DayZ folder, unreadable journals, and an external change each block with their own reason."""
        folder = self.interrupt()
        self.settings.value.dayz_root = None
        self.recover()
        self.assertEqual((self.operations.recovery_block, self.mutex.events), (NO_ROOT, []))
        self.settings.value.dayz_root = str(self.dayz)
        # A journal set that cannot be read is never acted on: nothing is written and the mutex is not taken
        records = self.storage.journals.records
        self.storage.journals.records = lambda: (_ for _ in ()).throw(OSError("unreadable"))
        before = self.tree(*self.roots)
        self.recover()
        self.assertEqual((self.operations.recovery_block, self.mutex.events), (UNREADABLE, []))
        self.assertEqual(self.tree(*self.roots), before)
        self.storage.journals.records = records
        # An external change that recovery must not delete keeps the earlier reason
        (folder / "serverDZ.cfg").write_bytes(b"external edit")
        self.recover()
        self.assertEqual(self.operations.recovery_block, ATTENTION)
        self.assertEqual((folder / "serverDZ.cfg").read_bytes(), b"external edit")
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_new_reason_has_an_operator_sentence(self) -> None:
        """The logged reason is worded by the catalogue: what happened and what to do."""
        sentence = wording.block_reason_text(NOT_STOPPED)
        self.assertEqual(sentence, (
            "A profile restore from a backup archive was interrupted and must be finished. This is possible only "
            "while the server is stopped and no other DayZ-ServerMan uses this DayZ installation. "
            "Stop the server, then restart DayZ-ServerMan."))
        self.assertFalse(wording.leaks_identifier(sentence))
        self.assertNotEqual(wording.block_reason_text(ATTENTION), sentence)
        # Only the run-time failure, which the next start undoes, asks for a stop and a restart
        for reason in (ATTENTION, NO_ROOT, UNREADABLE):
            self.assertNotIn("Stop the DayZ server", wording.block_reason_text(reason), reason)

    def build_through_composition(self, state: ServerState) -> tuple[Path, ...]:
        """Run the composition of the direct restore over the fixture's journals; return the watched roots."""
        self.lifecycle.state = state
        paths = SimpleNamespace(operations=self.root / "operations", profiles=self.repository.root,
                                backup_recovery=self.root / "recovery")
        # The composition reads its own journal folder, so the fixture's journals move there
        (self.root / "operations").mkdir()
        (self.root / "journals").rename(self.root / "operations" / "profile-restore-journals")
        roots = (self.dayz, self.repository.root, self.root / "operations" / "profile-restore-journals")
        self.before = self.tree(*roots)
        profile_restore_composition.build_profile_restore(
            paths, self.profiles, self.settings, self.backups, self.lifecycle, self.mutex, self.operations)
        return roots

    def test_composition_recovers_through_the_guard(self) -> None:
        """The composition builds the guard from its lifecycle and mutex: a running server blocks the recovery."""
        folder = self.interrupt()
        roots = self.build_through_composition(ServerState.RUNNING_MANAGED)
        self.assertEqual(self.tree(*roots), self.before)
        self.assertTrue(folder.exists())
        self.assertEqual(self.operations.recovery_block, NOT_STOPPED)
        self.assertEqual(self.mutex.events, GUARDED_RUN)

    def test_composition_reads_the_state_under_the_mutex(self) -> None:
        """With a stopped server the composition recovers, and the state is read only after the mutex is taken."""
        folder = self.interrupt()
        self.build_through_composition(ServerState.STOPPED)
        self.assertFalse(folder.exists())
        self.assertIsNone(self.operations.recovery_block)
        self.assertEqual(self.mutex.events, GUARDED_RUN)

if __name__ == "__main__":
    unittest.main()
