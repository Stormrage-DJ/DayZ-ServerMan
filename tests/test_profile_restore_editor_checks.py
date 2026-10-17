"""Checks of the editor group before the journal, journal version 2 and 1, and the review of the notes (10.11)."""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from dayz_serverman.application.restore_editor_files import record_bytes
from dayz_serverman.cli.output import render_blocks
from dayz_serverman.cli.review import profile_restore_review
from dayz_serverman.domain.mission_map_records import sealed
from dayz_serverman.domain.restore_editor_texts import EDITOR_NOTES_HEADING
from dayz_serverman.repositories.mission_map_records import RecordConflict
from dayz_serverman.repositories.profile_restore_editor import ProfileEditorGroup
from dayz_serverman.repositories.profile_restore_editor_shapes import EditorFile
from dayz_serverman.repositories.profile_restore_journal import validate_record
from tests.profile_restore_editor_fixtures import (
    AREA, MISSION_KEY, PREVIOUS, SUPERSEDE_KEY, VANILLA, Crash, ProfileEditorFixture, original_fields, sha,
)

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
FOLDER = f"{AREA}profiles/restored/{MISSION_KEY}/"


class PlanCheckTests(ProfileEditorFixture):
    """A file that is not a record of the new profile refuses the restore before its journal starts."""

    def refused(self, files: tuple[EditorFile, ...], error: type[Exception] = ValueError) -> None:
        """Assert that the restore refuses and writes no journal and no editor file."""
        before = self.tree()
        with self.assertRaises(error):
            self.apply("refused", files)
        self.assertEqual(self.journals.records(), [])
        self.assertEqual(self.tree(), before)

    def test_a_record_of_another_profile_is_refused(self) -> None:
        files = self.files("refused")
        self.refused((replace(files[0], relative=files[0].relative.replace("/restored/", "/main/")),))
        # A record whose fields name another association than its folder
        baseline = files[1]
        self.refused((replace(baseline, relative=baseline.relative.replace(MISSION_KEY, SUPERSEDE_KEY)),))

    def test_a_path_of_another_kind_or_shape_is_refused(self) -> None:
        files = self.files("refused")
        self.refused((replace(files[0], kind="baseline"),))
        self.refused((EditorFile("plan", "data/profiles/restored.json", b"{}"),))
        self.refused((EditorFile("unknown", files[0].relative, b"{}"),))

    def test_an_existing_target_is_refused_by_the_group(self) -> None:
        # The restore first moves such a remnant away (test_profile_restore_editor_remnants); the group never replaces it
        plan = self.area / "profiles" / "restored" / MISSION_KEY / "plan.json"
        plan.parent.mkdir(parents=True)
        plan.write_bytes(b"a remnant")
        with self.assertRaises(ValueError):
            ProfileEditorGroup(self.paths).plan("refused", "restored", self.files("refused"))
        self.assertEqual(plan.read_bytes(), b"a remnant")

    def test_content_needs_its_digest_and_an_original_that_the_restore_writes(self) -> None:
        self.refused((EditorFile("content", f"{FOLDER}files/{sha(VANILLA)}", PREVIOUS),))
        foreign = "d4" * 16
        self.refused((EditorFile("content", f"{AREA}originals/{foreign}/files/{sha(PREVIOUS)}", PREVIOUS),))

    def test_a_ledger_entry_of_another_profile_is_refused(self) -> None:
        entry = next(file for file in self.files("refused") if file.kind == "ledger")
        fields = {name: value for name, value in json.loads(entry.data).items()
                  if name not in ("schema_version", "revision", "manifest_sha256")}
        self.refused((replace(entry, data=record_bytes(sealed({**fields, "profile_id": "main"}), 0)),))

    def test_the_original_of_a_shared_target_is_never_superseded(self) -> None:
        (self.area / "profiles" / "other" / SUPERSEDE_KEY).mkdir(parents=True)
        self.refused(self.files("refused"), RecordConflict)

    def test_a_restored_copy_needs_a_different_active_original(self) -> None:
        group = ProfileEditorGroup(self.paths)
        incoming = sealed(original_fields(SUPERSEDE_KEY, "mission", PREVIOUS))
        copy_file = EditorFile("restored_copy", f"{FOLDER}restored-mission-copy-1.json", record_bytes(incoming, 0))
        planned = group.plan("copy-1", "restored", (copy_file,))
        self.assertEqual([item["relative"] for item in planned["files"]], [copy_file.relative])
        self.assertEqual(planned["created"], [f"{AREA}profiles", f"{AREA}profiles/restored", FOLDER[:-1]])
        same = sealed(original_fields(SUPERSEDE_KEY, "mission", VANILLA))
        with self.assertRaises(ValueError):
            group.plan("copy-1", "restored", (replace(copy_file, data=record_bytes(same, 0)),))
        # The operation in the name must be the restore operation
        with self.assertRaises(ValueError):
            group.plan("copy-2", "restored", (copy_file,))


class JournalVersionTests(ProfileEditorFixture):
    """Journal version 2 holds the editor group; version 1 stays as it was."""

    def test_version_2_holds_only_records_of_the_restored_profile(self) -> None:
        self.apply("checked")
        record = self.journal("checked")
        validate_record(record)
        changes = (
            lambda value: value.update(version=3),
            lambda value: value["groups"].pop(),
            lambda value: value.update(version=1),
            lambda value: value["groups"][3]["files"][0].update(relative=f"{AREA}profiles/main/{MISSION_KEY}/plan.json"),
            lambda value: value["groups"][3]["files"][0].update(before={"type": "file", "sha256": "e" * 64, "size": 1}),
            lambda value: value["groups"][3]["created"].append("data/profiles"),
            lambda value: value["groups"][3]["files"][0].update(kind="retired"),
            lambda value: value["groups"][3].update(files=[]),
        )
        for index, change in enumerate(changes):
            changed = copy.deepcopy(record)
            change(changed)
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_record(changed)

    def test_a_restore_without_editor_records_keeps_version_1_and_its_recovery(self) -> None:
        before = self.tree()
        self.storage.hook = lambda actual: (_ for _ in ()).throw(Crash()) if actual == "PUBLISHED_MISSION" else None
        with self.assertRaises(Crash):
            self.apply("plain", ())
        journal = self.journal("plain")
        self.assertEqual((journal["version"], [group["role"] for group in journal["groups"]]),
                         (1, ["generated", "mission", "profile"]))
        # Both a storage with the manager layout and one without it recover a version 1 journal
        self.assertFalse(self.new_storage(manager=False).inspect(self.dayz)["blocked"])
        self.assertEqual(self.journal("plain")["phase"], "ROLLED_BACK")
        self.storage.hook = lambda _actual: None
        self.apply("plain-2", ())
        self.assertEqual(self.journal("plain-2")["version"], 1)
        self.assertEqual(self.tree(), before)


class ReviewTests(unittest.TestCase):
    """The profile restore review prints the editor_notes sentences of the backup restore review."""

    PREVIEW = {"profile": {"profile_id": "restored", "display_name": "Restored"}, "mission_root": "m",
               "instance_id": 1, "storage_policy": "replace_existing", "affected_profile_ids": ["other"],
               "warnings": [], "missing_mods": [], "editor_notes": ["Note one.", "Note two."]}

    def test_the_cli_prints_the_heading_and_the_notes_before_the_replacement(self) -> None:
        text = render_blocks(profile_restore_review(self.PREVIEW, "archive.zip", {}))
        heading, first, second, other = (text.index(item) for item in (
            EDITOR_NOTES_HEADING, "Note one.", "Note two.", "other"))
        self.assertLess(heading, first)
        self.assertLess(first, second)
        self.assertLess(second, other)
        self.assertNotIn(EDITOR_NOTES_HEADING, render_blocks(profile_restore_review(
            {**self.PREVIEW, "editor_notes": []}, "archive.zip", {})))

    def test_the_window_shows_the_same_heading_and_one_paragraph_for_each_note(self) -> None:
        script = (FRONTEND / "profile_restore.js").read_text(encoding="utf-8")
        self.assertIn(f'directNode("h3", "{EDITOR_NOTES_HEADING}")', script)
        self.assertIn('notes.map((note) => directNode("p", String(note)))', script)
        self.assertIn('container.id = "direct-editor-notes";', script)
        self.assertLess(script.index("review.append(...notes);"), script.index('preview.storage_policy === "replace_existing"'))


if __name__ == "__main__":
    unittest.main()
