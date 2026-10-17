"""The `editor_notes` of the backup restore preview (Product Owner 2026-10-16 20:58:44 and 21:01:16, evidence 10.10).

The tests check one text for each row, the order, the CLI review and the window review.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dayz_serverman.cli.output import render_blocks as render_text
from dayz_serverman.cli.review import restore_review
from dayz_serverman.domain import restore_editor_texts as words
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.repositories.mission_map_records import OriginalStore
from tests.restore_editor_fixtures import APPLIED, BANDITS, VANILLA, RestoreEditorFixture, other_profile

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"


class EditorNoteTests(RestoreEditorFixture):
    """Each D5 row gives its Product Owner sentence, word for word, in the review order."""

    def notes(self, backup: str) -> list[str]:
        """Return the notes of the restore preview."""
        return self.restore_service().preview("main", backup)["editor_notes"]

    def replace_original(self) -> None:
        """Capture another mission original after the backup."""
        (self.area / "originals" / self.mission_key / "original.json").unlink()
        self.write_original(self.mission_key, data=b"<areaflags>later</areaflags>")

    def test_the_texts_are_the_product_owner_sentences(self) -> None:
        self.assertEqual(words.EDITOR_NOTES_HEADING, "Map editor records")
        self.assertTrue(words.CARRIED.startswith("This backup holds the map editor records of this profile."))
        self.assertEqual(words.NO_RECORDS, "This backup has no map editor records.")
        self.assertEqual(words.PLAN_SET_ASIDE, "The current map plan is not valid. The restore keeps it under a dated name.")
        self.assertEqual(set(words.SKIPPED), {"path_change", "mission_key", "mission_unresolved", "runtime_key"})
        self.assertEqual(words.profile_names(["gone", "main"], {"main": "Máin Profile"}),
                         "gone (deleted profile), Máin Profile")

    def test_no_original_and_a_shared_mission(self) -> None:
        self.write_all()
        backup = self.backup()
        self.clear_area()
        self.profiles.others.append(other_profile("other", None))
        # Row by row, the mission sentence first, then the runtime profile sentence (21:55:02)
        self.assertEqual(self.notes(backup), [words.CARRIED, words.NO_ORIGINAL, words.RUNTIME_NO_ORIGINAL,
                                              words.SHARED.format(profiles="other")])

    def test_the_runtime_profile_texts_of_rows_4_5_and_6(self) -> None:
        self.write_all()
        backup = self.backup()
        (self.area / "originals" / self.runtime_key / "original.json").unlink()
        self.write_original(self.runtime_key, "runtime", b'{"bandits": 3}')
        self.assertEqual(self.notes(backup), [words.CARRIED, words.RUNTIME_SUPERSEDED])
        # Another profile uses the runtime profile folder: row 5 for the runtime class, row 6 for the mission
        self.profiles.others.append(other_profile("other", "profiles\\main"))
        self.assertEqual(self.notes(backup), [words.CARRIED, words.RUNTIME_RESTORED_COPY.format(profiles="other"),
                                              words.SHARED.format(profiles="other")])
        self.assertTrue(words.RUNTIME_RESTORED_COPY.startswith("The saved original of the runtime profile folder"))

    def test_the_runtime_profile_text_of_row_6(self) -> None:
        self.write_all()
        backup = self.backup()
        self.clear_area()
        self.profiles.others.append(other_profile("other", "profiles\\main", "mpmissions/none"))
        self.assertEqual(self.notes(backup), [words.CARRIED, words.NO_ORIGINAL, words.RUNTIME_NO_ORIGINAL,
                                              words.RUNTIME_SHARED.format(profiles="other")])

    def test_the_same_digest_has_no_text(self) -> None:
        self.write_all()
        backup = self.backup()
        self.assertEqual(self.notes(backup), [words.CARRIED])

    def test_a_superseded_original(self) -> None:
        self.write_all()
        backup = self.backup()
        self.replace_original()
        self.assertEqual(self.notes(backup), [words.CARRIED, words.SUPERSEDED])

    def test_a_restored_copy_names_the_other_profiles(self) -> None:
        self.write_all()
        backup = self.backup()
        self.replace_original()
        (self.area / "profiles" / "gone" / self.mission_key).mkdir(parents=True)
        self.profiles.others.append(other_profile("other", None))
        self.assertEqual(self.notes(backup), [words.CARRIED, words.RESTORED_COPY.format(
            profiles="gone (deleted profile), other")])

    def test_an_old_archive_gives_row_7_parts_a_and_b(self) -> None:
        self.save_plan()
        backup = self.backup(editor=False)
        # Without a last Apply record the restore has no editor part
        self.assertNotIn("editor_notes", self.restore_service().preview("main", backup))
        self.write_original(self.mission_key)
        self.write_records()
        self.assertEqual(self.notes(backup), [words.NO_RECORDS, words.KEEPS_ORIGINAL])

    def test_a_carried_record_that_does_not_match_and_an_invalid_plan(self) -> None:
        self.save_plan()
        self.write_original(self.mission_key)
        self.write_original(self.runtime_key, "runtime", BANDITS)
        self.write_records(after=VANILLA)
        backup = self.backup()
        (self.folder() / "plan.json").write_bytes(b"{")
        self.assertEqual(self.notes(backup), [words.CARRIED, words.APPLIED_MISMATCH, words.PLAN_SET_ASIDE])

    def test_a_changed_row_makes_the_preview_stale(self) -> None:
        self.write_all()
        backup = self.backup()
        self.replace_original()
        service = self.restore_service()
        preview = service.preview("main", backup)
        self.assertEqual(preview["editor"]["rows"]["original_mission"], "supersede")
        # Another profile now resolves to the mission: the row becomes "Different original, shared"
        self.profiles.others.append(other_profile("other", None))
        manifest = service._backups.verified_manifest(self.paths.backups, backup, "main")
        with self.assertRaisesRegex(RevisionConflict, "stale"):
            service.apply("main", backup, 3, 4, manifest.manifest_digest, preview["fingerprint"], "restore-1",
                          lambda _phase, _percent: None)
        self.assertEqual(OriginalStore(self.area, self.mission_key).read()["files"][0]["size"], 28)
        self.assertEqual((self.mission / "areaflags.map").read_bytes(), APPLIED)

    def test_backup_restore_prints_the_notes_before_the_question(self) -> None:
        self.write_all()
        backup = self.backup()
        self.clear_area()
        preview = self.restore_service().preview("main", backup)
        text = render_text(restore_review(preview, lambda _value: "today"))
        heading, carried, question = (text.index(item) for item in (
            words.EDITOR_NOTES_HEADING, words.CARRIED, "Restore this backup?"))
        self.assertLess(text.index(preview["targets"][-1]["target_relative"]), heading)
        self.assertLess(heading, carried)
        self.assertLess(carried, question)
        # A restore without an editor part prints no heading
        self.assertNotIn(words.EDITOR_NOTES_HEADING, render_text(restore_review({"targets": []}, lambda _v: "")))

    def test_the_window_review_shows_the_heading_and_one_warning_for_each_note(self) -> None:
        script = (FRONTEND / "restore.js").read_text(encoding="utf-8")
        self.assertIn('restoreNode("h5", "", "Map editor records")', script)
        self.assertIn('restoreNode("p", "", String(note))', script)
        self.assertIn('container.id = "restore-editor-notes";', script)
        self.assertIn("summary.append(list, ...notes, apply);", script)


if __name__ == "__main__":
    unittest.main()
