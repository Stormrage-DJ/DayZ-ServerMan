"""A remnant editor folder of the new profile ID: the preview names it, the restore moves it and reports it (10.11).

Only an interrupted deletion of a profile with the same ID leaves such a folder (Architect 2026-10-17 05:41:39 in
evidence 10.11). The texts are of the Product Owner entry 2026-10-17 05:42:37.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from dayz_serverman.cli.commands.profile_restore import result_lines
from dayz_serverman.cli.output import render_blocks
from dayz_serverman.domain.models import RevisionConflict
from dayz_serverman.domain.restore_editor_texts import REMNANT_MOVED, REMNANT_NOTE
from dayz_serverman.repositories.profiles import ProfileNotFound
from tests.profile_restore_editor_fixtures import MISSION_KEY, ProfileEditorFixture

try:
    from _winapi import CreateJunction
except ImportError:  # pragma: no cover - only Windows has junctions
    CreateJunction = None


class RemnantTests(ProfileEditorFixture):
    """The remnant moves into the deleted folder before the journal starts; an empty folder is reused."""

    def setUp(self) -> None:
        """Leave the editor folder of an earlier profile `restored`, as an interrupted deletion does."""
        super().setUp()
        self.remnant = self.area / "profiles" / "restored"
        (self.remnant / MISSION_KEY).mkdir(parents=True)

    def leave_record(self) -> None:
        """Put a last-applied record of the earlier profile into its association folder."""
        (self.remnant / MISSION_KEY / "applied.json").write_bytes(b"record of the earlier profile")

    def test_the_texts_are_the_product_owner_sentences(self) -> None:
        self.assertEqual(REMNANT_NOTE, "Map editor records of an earlier profile with this ID are still in the "
                         "DayZ-ServerMan folder. An interrupted deletion left them. The restore moves them to the "
                         "folder for deleted records before it starts, and it does not use them.")
        self.assertEqual(REMNANT_MOVED, "Map editor records of an earlier profile with this ID were moved to the "
                         "folder for deleted records.")

    def test_a_remnant_with_a_record_moves_and_the_records_install(self) -> None:
        self.leave_record()
        self.assertEqual(self.service.preview(self.request)["editor_notes"], [REMNANT_NOTE])
        files = self.files("moved")
        result = self.apply("moved", files)
        self.assertEqual(result["editor_remnant_notice"], REMNANT_MOVED)
        # The earlier records keep their bytes below a dated name of the deleted folder
        moved = list((self.area / "deleted").iterdir())
        self.assertEqual(len(moved), 1)
        self.assertRegex(moved[0].name, r"^profile-restored-[0-9]{8}T[0-9]{6}Z$")
        self.assertEqual((moved[0] / MISSION_KEY / "applied.json").read_bytes(), b"record of the earlier profile")
        tree = self.tree()
        for file in files:
            self.assertEqual(tree["/".join(file.relative.split("/")[1:])], file.data, file.relative)
        # The result line follows the success line of the CLI, and JSON carries the same text
        text = render_blocks(result_lines(result))
        self.assertLess(text.index("Profile restored"), text.index(REMNANT_MOVED))

    def test_an_earlier_deleted_folder_of_the_same_name_is_never_replaced(self) -> None:
        self.leave_record()
        self.storage.editor_group._clock = lambda: datetime(2026, 10, 17, 6, 0, 0, tzinfo=UTC)  # type: ignore[union-attr]
        earlier = self.area / "deleted" / "profile-restored-20261017T060000Z"
        earlier.mkdir(parents=True)
        (earlier / "kept.json").write_bytes(b"earlier")
        self.apply("second")
        self.assertEqual((earlier / "kept.json").read_bytes(), b"earlier")
        self.assertTrue((earlier.parent / f"{earlier.name}-2" / MISSION_KEY / "applied.json").is_file())

    def test_an_empty_remnant_folder_is_reused_without_a_text(self) -> None:
        preview = self.service.preview(self.request)
        self.assertNotIn("editor_notes", preview)
        result = self.apply("reused")
        self.assertNotIn("editor_remnant_notice", result)
        self.assertFalse((self.area / "deleted").exists())
        self.assertTrue((self.remnant / MISSION_KEY / "plan.json").is_file())
        self.assertNotIn(REMNANT_MOVED, render_blocks(result_lines(result)))

    def test_the_fingerprint_binds_the_remnant_sentence(self) -> None:
        self.leave_record()
        preview = self.service.preview(self.request)
        (self.remnant / MISSION_KEY / "applied.json").unlink()
        self.assertNotEqual(self.service.preview(self.request)["preview_fingerprint"], preview["preview_fingerprint"])
        # The reviewed preview named a remnant that is gone now, so the apply refuses
        parameters = {**self.request, "expected_manifest_digest": preview["manifest_digest"],
                      "preview_fingerprint": preview["preview_fingerprint"], "overwrite_confirmation": None}
        with self.assertRaises(RevisionConflict):
            self.service.apply(parameters, "stale", lambda *_: None)
        self.assertEqual(self.journals.records(), [])

    def test_a_failed_move_refuses_before_the_journal(self) -> None:
        self.leave_record()
        with patch("dayz_serverman.repositories.profile_restore_editor.rename_directory",
                   side_effect=PermissionError("in use")), self.assertRaises(OSError):
            self.apply("failed")
        self.assertEqual(self.journals.records(), [])
        self.assertEqual((self.remnant / MISSION_KEY / "applied.json").read_bytes(), b"record of the earlier profile")
        with self.assertRaises(ProfileNotFound):
            self.profiles.read("restored")

    @unittest.skipIf(CreateJunction is None, "junctions are unavailable")
    def test_a_linked_remnant_refuses_before_the_journal(self) -> None:
        outside = self.root / "outside"
        (outside / MISSION_KEY).mkdir(parents=True)
        (outside / MISSION_KEY / "applied.json").write_bytes(b"outside")
        (self.remnant / MISSION_KEY).rmdir()
        self.remnant.rmdir()
        CreateJunction(str(outside), str(self.remnant))
        with self.assertRaises(ValueError):
            self.apply("linked")
        self.assertEqual(self.journals.records(), [])
        self.assertEqual((outside / MISSION_KEY / "applied.json").read_bytes(), b"outside")
        self.assertFalse((self.area / "deleted").exists())


if __name__ == "__main__":
    unittest.main()
