"""Skipped editor parts and invalidated content (Architect 2026-10-16 21:00:06 in evidence 10.10).

An editor part that does not fit the active target keys is skipped and never blocks the restore (ruling (5)).
The sweep keeps the content of an invalidated record (ruling (6)).
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_serverman.application.mission_configuration import MissionPathError
from dayz_serverman.domain.restore_editor_texts import KEEPS_ORIGINAL, SKIPPED
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from dayz_serverman.repositories.mission_map_journal_store import MissionMapJournalRepository
from dayz_serverman.repositories.json_store import VersionedJsonRepository
from dayz_serverman.repositories.mission_map_layout import RETIRED_FILE, TargetClass, invalidated_name, target_key
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.restore_editor_fixtures import APPLIED, BANDITS, MISSION_ROOT, MOMENT, PREVIOUS, VANILLA, RestoreEditorFixture, sha


class SkippedPartTests(RestoreEditorFixture):
    """One test for each row of the ruling (5) table: the payload is restored in every row."""

    def backup_then_change(self) -> str:
        """Back up all records, then change the mission file that the payload restore brings back."""
        self.write_all()
        backup = self.backup()
        self.write_mission(VANILLA)
        return backup

    def preview_editor(self, backup: str) -> tuple[dict, list[str]]:
        """Return the machine field and the notes of the restore preview."""
        preview = self.restore_service().preview("main", backup)
        return preview["editor"], preview["editor_notes"]

    def retire_old_folder(self) -> None:
        """Retire the folder of the old key, as an adoption after the move does, and take the new key."""
        new_key = target_key(TargetClass.MISSION, self.mission.resolve())
        VersionedJsonRepository(self.folder() / RETIRED_FILE, 1).save(
            {"operation_id": "adopt-1", "retired_at": "2026-10-16T13:00:00Z", "new_mission_key": new_key}, None)
        self.mission_key = new_key

    def test_the_path_change_state_restores_no_editor_file(self) -> None:
        backup = self.backup_then_change()
        # An unretired folder of the same mission root under another key starts the path-change state (D2)
        other = self.area / "profiles" / "main" / ("a" * 32)
        other.mkdir(parents=True)
        (other / "plan.json").write_text(json.dumps({"schema_version": 1, "revision": 0, "plan": {
            "association": {"mission_root": MISSION_ROOT}}}), encoding="utf-8")
        before = self.tree()
        editor, notes = self.preview_editor(backup)
        self.assertEqual((editor["skipped"], notes), (["path_change"], [SKIPPED["path_change"]]))
        self.restore(backup)
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)

    def test_an_archive_of_another_mission_key_restores_no_editor_file(self) -> None:
        backup = self.backup_then_change()
        # The DayZ folder moved: the same profile texts now resolve to other target keys
        moved = self.dayz.parent / "Moved Root"
        shutil.copytree(self.dayz, moved)
        self.settings.value.dayz_root = str(moved)
        self.mission, self.runtime = moved / "mpmissions" / "dayzOffline.chernarusplus", moved / "profiles" / "main"
        self.retire_old_folder()
        # The active key has its own last Apply record, which describes the restored files
        self.write_original(self.mission_key)
        self.write_records(after=APPLIED)
        before = self.tree()
        editor, notes = self.preview_editor(backup)
        self.assertEqual((editor["skipped"], notes), (["mission_key"], [SKIPPED["mission_key"], KEEPS_ORIGINAL]))
        self.assertEqual(editor["rows"]["applied_current"], "keep")
        self.restore(backup)
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)

    def test_the_active_key_invalidates_a_record_that_differs_from_the_restored_files(self) -> None:
        backup = self.backup_then_change()
        moved = self.dayz.parent / "Moved Root"
        shutil.copytree(self.dayz, moved)
        self.settings.value.dayz_root = str(moved)
        self.mission = moved / "mpmissions" / "dayzOffline.chernarusplus"
        self.retire_old_folder()
        self.write_original(self.mission_key)
        self.write_records(after=VANILLA)
        self.restore(backup)
        self.assertFalse((self.folder() / "applied.json").exists())
        self.assertTrue((self.folder() / invalidated_name(MOMENT, 1)).is_file())

    def test_a_mission_folder_that_cannot_be_resolved_restores_no_editor_file(self) -> None:
        backup = self.backup_then_change()
        before = self.tree()
        with patch.object(self.plans, "_resolve", side_effect=MissionPathError("unresolved")):
            editor, notes = self.preview_editor(backup)
            self.assertEqual((editor["skipped"], notes), (["mission_unresolved"], [SKIPPED["mission_unresolved"]]))
            self.restore(backup)
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)

    def test_a_carried_original_of_another_runtime_key_is_not_installed(self) -> None:
        other = self.dayz / "profiles" / "other"
        other.mkdir(parents=True)
        for name, runtime in (("unresolved", MissionPathError("unresolved")), ("other_key", [other.resolve()])):
            with self.subTest(case=name):
                self.write_all()
                backup = self.backup()
                self.clear_area()
                with patch("dayz_serverman.application.restore_editor._contained",
                           side_effect=runtime if isinstance(runtime, Exception) else lambda *_a, **_k: runtime[0]):
                    editor, notes = self.preview_editor(backup)
                    self.restore(backup, f"restore-{name}")
                self.assertEqual(editor["skipped"], ["runtime_key"])
                self.assertEqual(notes[1], SKIPPED["runtime_key"])
                self.assertEqual(notes[-1], KEEPS_ORIGINAL)
                # The mission class is restored; the runtime original is not installed
                self.assertIsNotNone(OriginalStore(self.area, self.mission_key).read())
                self.assertIsNone(OriginalStore(self.area, self.runtime_key).read())
                self.assertTrue((self.folder() / "applied.json").is_file())
                self.clear_area()


class InvalidatedContentTests(RestoreEditorFixture):
    """The content sweep and the orphan cleanup keep the content of an invalidated record (ruling (6))."""

    def test_a_later_sweep_keeps_the_content_of_an_invalidated_record(self) -> None:
        self.save_plan()
        self.write_original(self.mission_key)
        self.write_original(self.runtime_key, "runtime", BANDITS)
        self.write_records(after=VANILLA)
        backup = self.backup()
        self.clear_area()
        self.restore(backup)
        content = self.folder() / "files" / sha(PREVIOUS)
        # Only the invalidated record names this content; no current record does
        self.assertTrue((self.folder() / invalidated_name(MOMENT, 1)).is_file())
        self.assertFalse((self.folder() / "applied.json").exists())
        self.assertTrue(content.is_file())
        journal = SimpleNamespace(records=[SimpleNamespace(target_path=self.paths.relative(self.folder() / "baseline.json"))])
        CommitRecords(self.paths, MissionMapJournalRepository(self.paths.operations / "j"), lambda *_: None).sweep(journal)  # type: ignore[arg-type]
        self.assertTrue(content.is_file())
        cleanup_orphans(self.paths, self.locks)
        self.assertTrue(content.is_file())
        # Content that no record names still goes
        stray = self.folder() / "files" / sha(b"unused")
        stray.write_bytes(b"unused")
        cleanup_orphans(self.paths, self.locks)
        self.assertFalse(stray.exists())
        self.assertTrue(Path(content).is_file())


if __name__ == "__main__":
    unittest.main()
