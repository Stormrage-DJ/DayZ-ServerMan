"""Damaged editor records in a same-profile restore (Architect 2026-10-16 21:56:00 in evidence 10.10).

A damaged per-profile record gets a dated evidence name in the same journal, and then the normal rule applies.
A damaged protected original is left as it is, and its class is skipped (`original_corrupt`).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dayz_serverman.application.restore_editor_files import EditorArchive
from dayz_serverman.domain import restore_editor_texts as words
from dayz_serverman.repositories.mission_map_commit_records import CommitRecords
from dayz_serverman.repositories.mission_map_journal_store import MissionMapJournalRepository
from dayz_serverman.repositories.mission_map_layout import corrupt_name, set_aside_name
from dayz_serverman.repositories.mission_map_recovery_cleanup import cleanup_orphans
from tests.restore_editor_fixtures import APPLIED, MOMENT, VANILLA, RestoreEditorFixture, sha

DAMAGED = b"{damaged"


class DamagedRecordTests(RestoreEditorFixture):
    """Each damaged record is kept under its evidence name, and the restore still restores the server files."""

    def damage(self, *names: str) -> dict[str, bytes]:
        """Back up all records, damage the named files of the association folder, and return the backup tree."""
        self.write_all()
        self.backup_id = self.backup()
        before = self.tree()
        for name in names:
            (self.folder() / name).write_bytes(DAMAGED)
        self.write_mission(VANILLA)
        return before

    def test_damaged_plan_baseline_and_last_apply_record_are_set_aside(self) -> None:
        before = self.damage("plan.json", "baseline.json", "applied.json")
        preview = self.restore_service().preview("main", self.backup_id)
        # Row 9 for the plan, then the working baseline, then the last Apply record (21:56:40)
        self.assertEqual(preview["editor_notes"], [words.CARRIED, words.PLAN_SET_ASIDE, words.BASELINE_CORRUPT,
                                                   words.APPLIED_CORRUPT])
        self.restore(self.backup_id)
        folder, association = self.folder(), f"profiles/main/{self.mission_key}/"
        self.assertEqual((folder / set_aside_name(MOMENT)).read_bytes(), DAMAGED)
        for name in ("baseline.json", "applied.json"):
            self.assertEqual((folder / corrupt_name(name, MOMENT)).read_bytes(), DAMAGED)
            # The archived record installs, because it fits the originals and the restored files
            self.assertEqual((folder / name).read_bytes(), before[association + name])
        self.assertEqual(corrupt_name("applied.json", MOMENT), "applied.corrupt-20261016T180000Z.json")
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)
        self.assertIsNone(self.plans.load("main")["read_only_reason"])

    def test_an_old_archive_sets_a_damaged_baseline_aside_and_builds_it_again(self) -> None:
        self.write_all()
        backup = self.backup(editor=False)
        (self.folder() / "baseline.json").write_bytes(DAMAGED)
        self.assertEqual(self.restore_service().preview("main", backup)["editor_notes"],
                         [words.NO_RECORDS, words.KEEPS_ORIGINAL, words.BASELINE_CORRUPT])
        self.restore(backup)
        self.assertEqual((self.folder() / corrupt_name("baseline.json", MOMENT)).read_bytes(), DAMAGED)
        # D19 "Otherwise built again from the active original": both classes have an active original here
        rebuilt = self.envelope(self.folder() / "baseline.json")
        self.assertEqual(rebuilt["target_keys"], [self.mission_key, self.runtime_key])
        self.assertEqual(rebuilt["provenance"][-1]["source"], "profile_restore")
        # A damaged original gives its class no part
        (self.folder() / "baseline.json").write_bytes(DAMAGED)
        (self.area / "originals" / self.runtime_key / "original.json").write_bytes(DAMAGED)
        self.restore(backup, "restore-2")
        self.assertEqual(self.envelope(self.folder() / "baseline.json")["target_keys"], [self.mission_key])

    def test_a_damaged_last_apply_record_of_an_old_archive_is_set_aside(self) -> None:
        self.save_plan()
        backup = self.backup(editor=False)
        self.write_original(self.mission_key)
        self.write_records()
        (self.folder() / "applied.json").write_bytes(DAMAGED)
        self.assertEqual(self.restore_service().preview("main", backup)["editor_notes"],
                         [words.NO_RECORDS, words.KEEPS_ORIGINAL, words.APPLIED_CORRUPT])
        self.restore(backup)
        self.assertFalse((self.folder() / "applied.json").exists())
        self.assertEqual((self.folder() / corrupt_name("applied.json", MOMENT)).read_bytes(), DAMAGED)
        # A damaged record is evidence, not an invalidated record, so no adoption is needed for it
        self.assertIsNone(self.plans.load("main")["read_only_reason"])

    def test_damaged_content_gets_its_evidence_name(self) -> None:
        self.write_all()
        backup = self.backup()
        content = self.folder() / "files" / sha(VANILLA)
        content.write_bytes(DAMAGED)
        (self.folder() / "applied.json").write_bytes(DAMAGED)
        preview = self.restore_service().preview("main", backup)
        self.assertEqual(preview["editor"]["rows"]["content_corrupt"], [self.paths.relative(content)])
        # One sentence for the content files, after the record sentences (Product Owner 22:32:02)
        self.assertEqual(preview["editor_notes"], [words.CARRIED, words.APPLIED_CORRUPT, words.CONTENT_CORRUPT])
        self.restore(backup)
        self.assertEqual(content.read_bytes(), VANILLA)
        self.assertEqual(content.with_name(corrupt_name(content.name, MOMENT)).read_bytes(), DAMAGED)

    def test_a_later_sweep_runs_next_to_a_corrupt_file(self) -> None:
        self.damage("applied.json", "baseline.json")
        self.restore(self.backup_id)
        kept = [self.folder() / corrupt_name(name, MOMENT) for name in ("applied.json", "baseline.json")]
        journal = SimpleNamespace(records=[SimpleNamespace(target_path=self.paths.relative(self.folder() / "baseline.json"))])
        CommitRecords(self.paths, MissionMapJournalRepository(self.paths.operations / "j"), lambda *_: None).sweep(journal)  # type: ignore[arg-type]
        cleanup_orphans(self.paths, self.locks)
        self.assertTrue(all(path.read_bytes() == DAMAGED for path in kept))
        self.assertTrue((self.folder() / "files" / sha(VANILLA)).is_file())


class DamagedOriginalTests(RestoreEditorFixture):
    """A damaged protected original is skipped for its class, never moved, and never blocks the restore."""

    def test_original_content_is_written_only_from_verified_bytes(self) -> None:
        self.write_all()
        backup = self.backup()
        folder = self.area / "originals" / self.mission_key
        (folder / "original.json").unlink()
        content = folder / "files" / sha(VANILLA)
        content.write_bytes(DAMAGED)
        # Verified archive bytes: the damaged content keeps its evidence name, and the original installs
        self.restore(backup)
        self.assertEqual(content.read_bytes(), VANILLA)
        self.assertEqual(content.with_name(corrupt_name(content.name, MOMENT)).read_bytes(), DAMAGED)
        self.assertTrue((folder / "original.json").is_file())
        # Without bytes whose digest equals the name, the original counts as damaged and nothing is written there
        (folder / "original.json").unlink()
        content.write_bytes(DAMAGED)
        before = self.tree()
        read = EditorArchive.read

        def unverified(archive: EditorArchive, path: str) -> bytes:
            """Give the original content entries bytes whose digest is not their name."""
            return b"other bytes" if f"originals/{self.mission_key}/contents/" in path else read(archive, path)
        with patch.object(EditorArchive, "read", autospec=True, side_effect=unverified):
            preview = self.restore_service().preview("main", backup)
            self.restore(backup, "restore-2")
        self.assertEqual((preview["editor"]["skipped"], preview["editor"]["rows"]["original_mission"]),
                         (["original_corrupt"], "corrupt"))
        self.assertEqual(preview["editor_notes"][1], words.ORIGINAL_CORRUPT["mission"])
        after = self.tree()
        mission = f"originals/{self.mission_key}/"
        self.assertEqual({k: v for k, v in after.items() if k.startswith(mission)},
                         {k: v for k, v in before.items() if k.startswith(mission)})
        self.assertFalse((folder / "original.json").exists())

    def test_a_damaged_original_skips_its_class(self) -> None:
        for classes in (("mission",), ("runtime",), ("mission", "runtime")):
            with self.subTest(classes=classes):
                self.write_all()
                backup = self.backup()
                keys = {"mission": self.mission_key, "runtime": self.runtime_key}
                for name in classes:
                    (self.area / "originals" / keys[name] / "original.json").write_bytes(DAMAGED)
                self.write_mission(VANILLA)
                preview = self.restore_service().preview("main", backup)
                self.assertEqual(preview["editor"]["skipped"], ["original_corrupt"])
                self.assertEqual(preview["editor_notes"],
                                 [words.CARRIED, *(words.ORIGINAL_CORRUPT[name] for name in classes)])
                self.restore(backup, f"restore-{len(classes)}-{classes[0]}")
                for name in classes:
                    self.assertEqual((self.area / "originals" / keys[name] / "original.json").read_bytes(), DAMAGED)
                    self.assertEqual(list((self.area / "originals" / keys[name]).glob("superseded-*")), [])
                self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)
                self.clear_area()


if __name__ == "__main__":
    unittest.main()
