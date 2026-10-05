"""QF-069 (D19): the three startup paths tag their "no DayZ server folder" block; tests (l), (p), (q), (r), (s)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The modules are imported, not the classes, so the loader does not run their cases here again
import test_mod_publication_startup as publication_startup  # noqa: E402
import test_profile_restore_startup_guard as direct_startup  # noqa: E402
from test_restore_startup_guard import (  # noqa: E402
    NO_ROOT, NOT_STOPPED, UNRESOLVED, RestoreGuardFixture,
)
from test_settings_missing_root_repair import build_repair, save_and_wait, server_folder  # noqa: E402
from startup_guard_fixtures import GUARDED_RUN  # noqa: E402
from dayz_serverman.application.operations.manager import OperationManager  # noqa: E402
from dayz_serverman.application.operations.recovery_blocks import MISSING_DAYZ_ROOT  # noqa: E402
from dayz_serverman.application.operations.store import OperationStore  # noqa: E402
from dayz_serverman.application.restore_coordinator import RestoreCoordinator  # noqa: E402
from dayz_serverman.application.settings_repair import NOT_THE_USED_FOLDER  # noqa: E402
from dayz_serverman.application.startup_recoveries import recover_interrupted_restores  # noqa: E402
from dayz_serverman.bridge.facade import ApplicationCallError  # noqa: E402
from dayz_serverman.domain.lifecycle import ServerState  # noqa: E402

PUBLICATION_NO_ROOT, PUBLICATION_NOT_STOPPED = publication_startup.NO_ROOT, publication_startup.NOT_STOPPED
DIRECT_NO_ROOT, DIRECT_NOT_STOPPED = direct_startup.NO_ROOT, direct_startup.NOT_STOPPED


def tagged(operations: OperationManager) -> bool:
    """Report whether every active block carries the "no DayZ server folder" cause."""
    return operations._recovery_blocks.only_cause(MISSING_DAYZ_ROOT)


def request(folder: Path) -> dict:
    """Return a repair request for unconfigured stored settings."""
    return {"expected_revision": None, "dayz_root": str(folder), "steamcmd_root": None, "custom_backup_root": None}


class RestoreWithoutFolderTests(RestoreGuardFixture, unittest.TestCase):
    """An interrupted backup restore and no DayZ server folder, then the repair save and a restart."""

    def setUp(self) -> None:
        """Interrupt a restore, give its folder the server program, and start without a folder."""
        super().setUp()
        self.before = self.interrupt()
        server_folder(self.dayz)
        self.settings.value.dayz_root = None
        self.built = build_repair(self.root / "repair", self.operations, restore_journals=self.journals,
                                  backup_recovery=self.recovery)

    def recover(self, operations: OperationManager) -> None:
        """Run the startup recovery of the backup restore with the guard over the doubles."""
        recover_interrupted_restores(self.journals, self.guarded_service(), operations)

    def restart(self, folder: str) -> OperationManager:
        """Start again: a new lane, and the folder that the repair saved."""
        self.settings.value.dayz_root = folder
        operations = OperationManager(OperationStore(self.root / "restart-operations"))
        self.addCleanup(operations.shutdown, 2)
        self.mutex.events.clear()
        self.recover(operations)
        return operations

    def test_p_no_folder_gives_its_own_tagged_reason_without_the_mutex(self) -> None:
        """(p) The new reason, owner RESTORE_BACKUP and the cause; the other causes keep theirs, untagged."""
        self.recover(self.operations)
        self.assertEqual(self.operations.recovery_block_pair(), (NO_ROOT, "RESTORE_BACKUP"))
        self.assertTrue(tagged(self.operations))
        self.assertEqual(self.mutex.events, [])
        self.settings.value.dayz_root = str(self.dayz)
        for state, reason in ((ServerState.RUNNING_MANAGED, NOT_STOPPED), (ServerState.STOPPED, UNRESOLVED)):
            with self.subTest(reason=reason):
                self.operations.clear_recovery_block()
                self.lifecycle.state = state
                if state == ServerState.STOPPED:
                    self.target.write_bytes(b"changed by hand")
                self.recover(self.operations)
                self.assertEqual(self.operations.recovery_block, reason)
                self.assertFalse(tagged(self.operations))

    def test_l_r_the_folder_must_match_the_journal_and_the_restart_recovers(self) -> None:
        """(l) Another folder is refused; (r) the used folder is saved and the next start recovers in the guard."""
        self.recover(self.operations)
        other = server_folder(self.root / "Other DayZ")
        refused = save_and_wait(self, self.built.coordinator, self.operations, request(other))
        self.assertEqual((refused.state.value, refused.terminal_error.code, refused.terminal_error.message),
                         ("FAILED", "PATH_INVALID", NOT_THE_USED_FOLDER))
        saved = save_and_wait(self, self.built.coordinator, self.operations, request(self.dayz))
        self.assertEqual(saved.state.value, "SUCCEEDED", saved.terminal_error)
        self.assertEqual(self.mutex.events, [])
        restarted = self.restart(self.built.settings.load().dayz_root)
        self.assertEqual(self.mutex.events, GUARDED_RUN)
        self.assertEqual((restarted.recovery_block, self.journals.records()), (None, ()))
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_r_a_server_that_is_not_stopped_blocks_again_without_the_cause(self) -> None:
        """(r) The restart finds a running server: the not-stopped block, and a repair save is refused."""
        self.recover(self.operations)
        self.assertEqual(save_and_wait(self, self.built.coordinator, self.operations, request(self.dayz)).state.value,
                         "SUCCEEDED")
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        restarted = self.restart(self.built.settings.load().dayz_root)
        self.assertEqual((restarted.recovery_block, tagged(restarted)), (NOT_STOPPED, False))
        coordinator = build_repair(self.root / "repair", restarted, restore_journals=self.journals,
                                   backup_recovery=self.recovery).coordinator
        stored = self.built.settings.load()
        with self.assertRaises(ApplicationCallError) as blocked:
            coordinator.save_settings({**request(self.dayz), "expected_revision": stored.revision})
        self.assertEqual(blocked.exception.code.value, "MUTATION_CONFLICT")

    def test_s_the_backups_check_after_the_save_lifts_only_the_restore_block(self) -> None:
        """(s) After the repair save, opening Backups recovers the restore and lifts only RESTORE_BACKUP."""
        self.operations.block_for_missing_dayz_root(PUBLICATION_NO_ROOT)
        self.recover(self.operations)
        self.assertEqual(save_and_wait(self, self.built.coordinator, self.operations, request(self.dayz)).state.value,
                         "SUCCEEDED")
        self.settings.value.dayz_root = self.built.settings.load().dayz_root
        inspection = RestoreCoordinator(self.guarded_service(), self.operations).inspect_restore_recovery({})
        self.assertEqual(inspection, {"blocked": False, "diagnostics": []})
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertEqual(self.operations.recovery_block, PUBLICATION_NO_ROOT)


class DirectRestoreWithoutFolderTests(direct_startup.ProfileRestoreStartupRecoveryTests):
    """An interrupted direct profile restore and no DayZ server folder."""

    def test_q_l_the_no_folder_block_is_tagged_and_the_folder_must_match(self) -> None:
        """(q) Only the no-folder branch is tagged; (l) another folder is refused, the used one saved."""
        self.interrupt()
        built = build_repair(self.root / "repair", self.operations, profile_storage=self.storage)
        self.settings.value.dayz_root = None
        self.recover()
        self.assertEqual((self.operations.recovery_block, tagged(self.operations)), (DIRECT_NO_ROOT, True))
        other = server_folder(self.root / "Other DayZ")
        refused = save_and_wait(self, built.coordinator, self.operations, request(other))
        self.assertEqual((refused.terminal_error.code, refused.terminal_error.message),
                         ("PATH_INVALID", NOT_THE_USED_FOLDER))
        saved = save_and_wait(self, built.coordinator, self.operations, request(self.dayz))
        self.assertEqual(saved.state.value, "SUCCEEDED", saved.terminal_error)
        # The other branches keep cause None
        self.operations.clear_recovery_block()
        self.settings.value.dayz_root = str(self.dayz)
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        self.recover()
        self.assertEqual((self.operations.recovery_block, tagged(self.operations)), (DIRECT_NOT_STOPPED, False))


class PublicationWithoutFolderTests(publication_startup.PublicationStartupRecoveryTests):
    """An interrupted mod publication and no DayZ server folder."""

    def test_q_only_the_no_folder_branch_is_tagged(self) -> None:
        """(q) No folder: tagged; a server that is not stopped: not tagged."""
        self.interrupt()
        dayz = self.settings.load().dayz_root
        self.settings.load = lambda: type("Settings", (), {"dayz_root": None})()
        self.recover()
        self.assertEqual((self.operations.recovery_block, tagged(self.operations)), (PUBLICATION_NO_ROOT, True))
        self.operations.clear_recovery_block()
        self.settings.load = lambda: type("Settings", (), {"dayz_root": dayz})()
        self.lifecycle.state = ServerState.RUNNING_MANAGED
        self.recover()
        self.assertEqual((self.operations.recovery_block, tagged(self.operations)), (PUBLICATION_NOT_STOPPED, False))


# The inherited cases run in their own modules only
for _subclass in (DirectRestoreWithoutFolderTests, PublicationWithoutFolderTests):
    for _klass in _subclass.__mro__[1:]:
        for _name in [name for name in vars(_klass) if name.startswith("test_")]:
            if _name not in vars(_subclass):
                setattr(_subclass, _name, None)
# The loop names would otherwise be found as a test class a second time
del _subclass, _klass, _name


if __name__ == "__main__":
    unittest.main()
