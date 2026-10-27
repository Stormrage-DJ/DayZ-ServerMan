"""QF-069 settings repair validates readable editor journals without running recovery."""

from __future__ import annotations

from dayz_serverman.application.operations.manager import OperationManager
from dayz_serverman.application.operations.models import OperationFailure
from dayz_serverman.application.operations.store import OperationStore
from dayz_serverman.application.settings_repair import NOT_THE_USED_FOLDER, SettingsRepair
from dayz_serverman.domain.models import SettingsInput
from dayz_serverman.repositories.profile_restore_journal import ProfileRestoreJournal
from dayz_serverman.repositories.profile_restore_storage import ProfileRestoreStorage
from dayz_serverman.repositories.restore_journal import RestoreJournalRepository
from tests.test_mission_map_journal_paths import snapshot
from tests.test_mission_map_recovery_phases import RecoveryFixture
from tests.test_settings_missing_root_repair import build_repair, server_folder


class EditorSettingsRepairTests(RecoveryFixture):
    """Matching roots pass the repair; different roots refuse before the settings replace."""

    def setUp(self):
        """Add real settings and path diagnostics to the disposable editor fixture."""
        super().setUp()
        self.operations = OperationManager(OperationStore(self.paths.root / "repair-ops"))
        self.addCleanup(self.operations.shutdown, 2)
        built = build_repair(self.paths.root, self.operations)
        self.settings = built.settings
        self.current = self.settings.save(SettingsInput(), None)
        self.repair = SettingsRepair(
            self.settings, RestoreJournalRepository(self.paths.root / "restore"), self.paths.backup_recovery,
            ProfileRestoreStorage(ProfileRestoreJournal(self.paths.root / "profile-restore"), self.paths.profiles,
                                  self.paths.backup_recovery), editor_recovery=self.recovery())
        server_folder(self.dayz)

    def request(self, root):
        """Return the existing repair shape with no changes outside the DayZ root."""
        return {"dayz_root": str(root), "steamcmd_root": None, "custom_backup_root": None}

    def test_matching_readable_editor_journal_accepts_only_settings_save(self):
        """The repair neither lifts a recovery block nor changes editor or DayZ state."""
        self.crash_at("AFTER_PREPARED")
        dayz_before, editor_before = snapshot(self.dayz), snapshot(self.paths.mission_map)
        journal_before = snapshot(self.repository.root)
        self.events.clear()
        saved = self.repair.save(self.request(self.dayz), self.current.revision)
        self.assertEqual(saved.dayz_root, str(self.dayz))
        self.assertEqual(snapshot(self.dayz), dayz_before)
        self.assertEqual(snapshot(self.paths.mission_map), editor_before)
        self.assertEqual(snapshot(self.repository.root), journal_before)
        self.assertEqual(self.events, [])

    def test_different_root_is_refused_without_settings_or_editor_write(self):
        """A readable editor journal binds repair to the installation that the journal used."""
        self.crash_at("AFTER_PREPARED")
        other = server_folder(self.paths.root / "OtherDayZ")
        before = self.settings._repository.path.read_bytes()
        with self.assertRaises(OperationFailure) as refused:
            self.repair.save(self.request(other), self.current.revision)
        self.assertEqual(refused.exception.code, "PATH_INVALID")
        self.assertEqual(str(refused.exception), NOT_THE_USED_FOLDER)
        self.assertEqual(self.settings._repository.path.read_bytes(), before)

    def test_unreadable_editor_record_does_not_invent_a_root_binding(self):
        """An unreadable record remains for fail-closed recovery after the restart."""
        path = self.repository.root / "broken.json"
        path.write_bytes(b"broken")
        saved = self.repair.save(self.request(self.dayz), self.current.revision)
        self.assertEqual(saved.dayz_root, str(self.dayz))
        self.assertEqual(path.read_bytes(), b"broken")
