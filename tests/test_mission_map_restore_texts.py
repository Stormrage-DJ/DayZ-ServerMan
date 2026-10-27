"""The Product Owner texts of 2026-10-15 07:10:16 in evidence 8.8, word for word, and where the preview shows them."""

from __future__ import annotations

import unittest

from dayz_serverman.domain import mission_map_restore_texts as texts
from tests.mission_map_restore_support import PLAN, RestoreFixture

PROTO, DIRT, TERRITORY = "mapgroupproto.xml", "mapgroupdirt.xml", "env/zombie_territories.xml"
FARM, VILLAGE = b'<usage name="Farm" />', b'<usage name="Village" />'


class WordingTests(unittest.TestCase):
    """Each text equals the Product Owner text."""

    def test_texts_word_for_word(self) -> None:
        """Refusals, stale review, late change, shared target, both backup lines and the kept-changes sentence."""
        path = "cfgeventspawns.xml"
        self.assertEqual(texts.NO_ORIGINAL_TEXT, (
            "Restore original is not available for this mission. DayZ-ServerMan has no saved original of this mission "
            "folder. It saves the original before the first map editor Apply on the folder."))
        self.assertEqual(texts.NO_PREVIOUS_STATE_TEXT, (
            "Restore previous state is not available for this mission. DayZ-ServerMan has no saved previous state of "
            "this mission for this profile. It saves one when this profile applies a map plan to this mission or uses "
            "Restore original on it."))
        self.assertEqual(texts.INVALID_MODE_TEXT,
                         "This restore choice is not valid. Choose Restore original or Restore previous state.")
        self.assertEqual(texts.RESTORE_STALE_TEXT, (
            "The profile, the settings, the mission files or the map editor records changed after the preview. Nothing "
            "was restored. Preview the restore again and check it before you restore."))
        self.assertEqual(texts.TARGET_CHANGED_TEXT.format(path=path), (
            "Restore stopped because cfgeventspawns.xml changed after the preview. Nothing was restored, and that "
            "file keeps its current content. Preview the restore again and check it before you restore."))
        self.assertEqual(texts.SHARED_ORIGINAL_TEXT, (
            "Other profiles use this mission folder or have map editor records for it. The folder has one original "
            "for all profiles, and Restore original writes that original. The restore also changes the mission files "
            "for every profile that uses this folder."))
        self.assertEqual(texts.BACKUP_TEXT.format(path=path), (
            "cfgeventspawns.xml changed outside the map editor. DayZ-ServerMan saves a copy of the current file "
            "before the restore changes it."))
        self.assertEqual(texts.DELETED_TEXT.format(path=path),
                         "cfgeventspawns.xml was deleted outside the map editor. The restore writes this file again.")
        self.assertEqual(texts.TARGET_UNPROJECTABLE_TEXT.format(path=path), (
            "This restore is not available for this mission. In the version of cfgeventspawns.xml that it would "
            "write, DayZ-ServerMan cannot read the values that the map editor sets, so it does not write that "
            "version back."))
        self.assertEqual(texts.KEPT_CHANGES_TEXT, (
            "This profile kept changes made outside the map editor with Keep the change. Restore original does not "
            "remove them from the map editor records. The next Apply writes them again with the map plan, and its "
            "preview shows them."))


class PreviewTextTests(RestoreFixture):
    """The backup lines and the kept-changes sentence in the restore preview."""

    def setUp(self) -> None:
        """Apply the plan once."""
        super().setUp()
        self.save(PLAN, 25)
        self.run_apply()

    def test_backup_line_for_an_edited_and_a_deleted_file(self) -> None:
        """An edited file gets the copy line; a deleted file gets the line that it is written again."""
        self.write(PROTO, (self.mission / PROTO).read_bytes().replace(FARM, VILLAGE))
        self.write(DIRT, None)
        lines = {item["path"]: item["text"] for item in self.restore_review("original").answer["external_changes"]}
        self.assertEqual(lines, {PROTO: texts.BACKUP_TEXT.format(path=PROTO),
                                 DIRT: texts.DELETED_TEXT.format(path=DIRT)})

    def test_kept_changes_sentence_only_with_kept_changes(self) -> None:
        """Without Keep the change the sentence is absent; after it, Restore original shows it, Restore previous not."""
        self.assertIsNone(self.restore_review("original").answer["original"]["kept_changes"])
        # An admin comment outside the marked territory block can be kept (8.7)
        edited = (self.mission / TERRITORY).read_bytes().replace(b"<territory-type>\r\n",
                                                                 b"<territory-type>\r\n<!-- admin -->\r\n")
        self.write(TERRITORY, edited)
        self.run_apply({"external_changes": {TERRITORY: "incorporate"}, "unavailable_encounters": {}})
        answer = self.restore_review("original").answer
        self.assertEqual(answer["original"]["kept_changes"], texts.KEPT_CHANGES_TEXT)
        self.assertIsNone(self.restore_review("previous").answer["original"])
        # The baseline keeps the change after Restore original, so the sentence stays (ruling 1a)
        self.run_restore("original", answer["fingerprint"])
        self.assertEqual(self.restore_review("original").answer["original"]["kept_changes"], texts.KEPT_CHANGES_TEXT)


if __name__ == "__main__":
    unittest.main()
