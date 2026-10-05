"""QF-069 (D19): the repair save sets only the DayZ server folder; tests (i) to (o) and j2. Fakes and temp folders only."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from tests.ui_harness_support import ROOT
except ModuleNotFoundError:
    from ui_harness_support import ROOT

from dayz_serverman.adapters.windows.diagnostics import WindowsPathDiagnostics
from dayz_serverman.application import activity_wording as wording
from dayz_serverman.application.coordinator import ApplicationCoordinator
from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import TERMINAL_STATES, OperationFailure
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.application.settings import SETTINGS_FIELDS, SettingsService
from dayz_serverman.application.settings_repair import NOT_A_SERVER_FOLDER, SettingsRepair
from dayz_serverman.bridge.facade import ApplicationCallError
from dayz_serverman.domain.models import SettingsInput
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.paths import PortablePaths
from dayz_serverman.repositories.profile_restore_journal import ProfileRestoreJournal
from dayz_serverman.repositories.profile_restore_storage import ProfileRestoreStorage
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository

# A startup reason that carries the cause
PUBLICATION_NO_ROOT = "Mutations are blocked by unresolved mod publication."
SOURCE = ROOT / "runnable" / "src" / "python" / "dayz_serverman" / "application" / "settings_repair.py"


def build_repair(base: Path, operations: OperationManager, *, restore_journals=None, backup_recovery=None,
                 profile_storage=None, reparse=frozenset()) -> SimpleNamespace:
    """Build a real settings service and the repair save over disposable folders and the given lane."""
    paths = PortablePaths.from_root(base / "Manager")
    paths.create_layout()
    diagnostics = WindowsPathDiagnostics(reparse_check=lambda path: str(path) in reparse)
    settings = SettingsService(VersionedJsonRepository(paths.manager_config), paths, diagnostics)
    repair = SettingsRepair(
        settings, restore_journals or RestoreJournalRepository(base / "restore-journals"),
        backup_recovery or paths.backup_recovery,
        profile_storage or ProfileRestoreStorage(ProfileRestoreJournal(base / "pr-journals"), paths.profiles,
                                                 paths.backup_recovery))
    return SimpleNamespace(paths=paths, settings=settings, repair=repair,
                           coordinator=ApplicationCoordinator(settings, operations, None, repair))


def server_folder(path: Path) -> Path:
    """Create a DayZ server folder with its program and return it."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "DayZServer_x64.exe").write_bytes(b"fixture")
    return path


def save_and_wait(test: unittest.TestCase, coordinator, operations, parameters: dict):
    """Submit a settings save and return its terminal record."""
    accepted = coordinator.save_settings(parameters)
    deadline = time.monotonic() + 5
    while operations.get(accepted["operation_id"]).state not in TERMINAL_STATES:
        test.assertLess(time.monotonic(), deadline, "the save did not finish")
        time.sleep(0.01)
    return operations.get(accepted["operation_id"])


class SettingsRepairTests(unittest.TestCase):
    """A blocked lane, stored settings with hand-edited derived paths, and a valid DayZ server folder."""

    def setUp(self) -> None:
        """Store settings without a DayZ server folder and set one cause-tagged block."""
        temporary = tempfile.TemporaryDirectory(prefix="serverman_repair_")
        self.addCleanup(temporary.cleanup)
        # The resolved form, so a short temporary name compares with the stored (resolved) path
        self.base = Path(temporary.name).resolve()
        self.operations = OperationManager(OperationStore(self.base / "ops"))
        self.addCleanup(self.operations.shutdown, 2)
        self.dayz = server_folder(self.base / "DayZ A")
        self.reparse = server_folder(self.base / "DayZ Link")
        self.built = build_repair(self.base, self.operations, reparse=frozenset({str(self.reparse)}))
        steam = self.base / "Steam CMD"
        (steam / "tools").mkdir(parents=True)
        self.stored = self.built.settings.save(SettingsInput(
            steamcmd_root=str(steam), steamcmd_executable=str(steam / "tools" / "steamcmd.exe"),
            workshop_content_root=str(self.base / "Workshop Elsewhere"), custom_backup_root=str(self.base / "Backups"),
            steam_account_name="Operator_1", steam_authentication_mode="ACCOUNT"), None)
        self.file = self.built.paths.manager_config
        self.operations.block_for_missing_dayz_root(PUBLICATION_NO_ROOT)

    def request(self, **changes) -> dict:
        """Return a repair request: a DayZ folder, the stored SteamCMD folder in other case, the stored backups."""
        return {"expected_revision": self.stored.revision, "dayz_root": str(self.dayz),
                "steamcmd_root": self.stored.steamcmd_root.lower(),
                "custom_backup_root": self.stored.custom_backup_root, **changes}

    def save(self, **changes):
        """Run one save through the coordinator and the lane."""
        return save_and_wait(self, self.built.coordinator, self.operations, self.request(**changes))

    def test_i_a_valid_folder_saves_only_the_folder_and_keeps_the_block(self) -> None:
        """(i) Only the two DayZ fields change; the result asks for the restart; the block and the tree stay."""
        tree = {path: path.read_bytes() for path in self.dayz.rglob("*") if path.is_file()}
        record = self.save()
        self.assertEqual(record.state.value, "SUCCEEDED", record.terminal_error)
        self.assertIs(record.result["restart_required"], True)
        saved = self.built.settings.load()
        self.assertEqual(Path(saved.dayz_root), self.dayz)
        self.assertEqual(Path(saved.dayz_executable), self.dayz / "DayZServer_x64.exe")
        for field in SETTINGS_FIELDS[2:]:
            self.assertEqual(getattr(saved, field), getattr(self.stored, field), field)
        self.assertEqual(saved.revision, self.stored.revision + 1)
        self.assertEqual(self.operations.recovery_block, PUBLICATION_NO_ROOT)
        self.assertEqual({path: path.read_bytes() for path in self.dayz.rglob("*") if path.is_file()}, tree)
        # The repair takes no mutex, reads no server state, lifts no block and never asks for recovery
        source = SOURCE.read_text(encoding="utf-8")
        for word in ("_mutex", ".guard(", ".stopped(", "lifecycle", "InstallationGuard", "clear_recovery_block",
                     "recovery_required=True"):
            self.assertNotIn(word, source)

    def test_j_other_shapes_are_refused_at_once(self) -> None:
        """(j) Another SteamCMD or backup folder, or no folder: refused by the block, the file unchanged."""
        before = self.file.read_bytes()
        for changes in ({"steamcmd_root": str(self.base / "Other Steam")}, {"custom_backup_root": None},
                        {"dayz_root": None}, {"dayz_root": "   "}):
            with self.subTest(changes=changes), self.assertRaises(ApplicationCallError) as refused:
                self.built.coordinator.save_settings(self.request(**changes))
            self.assertEqual((refused.exception.code.value, refused.exception.details),
                             ("MUTATION_CONFLICT", {"reason": "RECOVERY_BLOCK"}))
        self.assertEqual(self.file.read_bytes(), before)

    def test_j2_the_lane_step_checks_the_shape_again(self) -> None:
        """j2: called directly with a request that is no repair, the step fails and writes nothing."""
        before = self.file.read_bytes()
        for changes in ({"steamcmd_root": str(self.base / "Other Steam")}, {"custom_backup_root": None}):
            with self.subTest(changes=changes), self.assertRaises(OperationFailure) as failed:
                self.built.repair.save(self.request(**changes), self.stored.revision)
            self.assertEqual((failed.exception.code, failed.exception.recovery_required), ("REVISION_CONFLICT", False))
        self.assertEqual(self.file.read_bytes(), before)

    def test_k_a_folder_that_is_not_a_server_folder_is_refused(self) -> None:
        """(k) Missing, a file, no program, or a reparse point: PATH_INVALID, nothing written, the block stays."""
        before = self.file.read_bytes()
        (self.base / "a file").write_bytes(b"x")
        (self.base / "Empty DayZ").mkdir()
        for folder in ("Missing DayZ", "a file", "Empty DayZ", "DayZ Link"):
            with self.subTest(folder=folder):
                record = self.save(dayz_root=str(self.base / folder))
                self.assertEqual((record.state.value, record.terminal_error.code, record.terminal_error.message),
                                 ("FAILED", "PATH_INVALID", NOT_A_SERVER_FOLDER))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.operations.recovery_block, PUBLICATION_NO_ROOT)
        self.assertFalse(wording.leaks_identifier(NOT_A_SERVER_FOLDER))

    def test_m_a_stale_revision_conflicts(self) -> None:
        """(m) A stale revision gives REVISION_CONFLICT and writes nothing."""
        before = self.file.read_bytes()
        record = self.save(expected_revision=self.stored.revision + 3)
        self.assertEqual((record.state.value, record.terminal_error.code), ("FAILED", "REVISION_CONFLICT"))
        self.assertEqual(self.file.read_bytes(), before)

    def test_n_without_a_block_the_save_is_the_normal_save(self) -> None:
        """(n) No block: a save that sets only a missing folder is saved as today, without the repair rules."""
        self.operations.clear_recovery_block()
        missing = self.base / "Missing DayZ"
        record = self.save(dayz_root=str(missing))
        self.assertEqual(record.state.value, "SUCCEEDED", record.terminal_error)
        self.assertNotIn("restart_required", record.result)
        self.assertEqual(Path(self.built.settings.load().dayz_root), missing)

    def test_o_a_second_repair_save_with_the_new_revision_is_accepted(self) -> None:
        """(o) Two repair saves in turn: the second uses the new revision."""
        first = self.save()
        self.assertEqual(first.state.value, "SUCCEEDED")
        other = server_folder(self.base / "DayZ B")
        second = self.save(dayz_root=str(other), expected_revision=first.result["revision"])
        self.assertEqual(second.state.value, "SUCCEEDED", second.terminal_error)
        self.assertEqual(Path(self.built.settings.load().dayz_root), other)


if __name__ == "__main__":
    unittest.main()
