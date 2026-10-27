"""Leaf 10.13: the adoption review of a true move, a copy after a path change and a copy after a capture refusal."""

from __future__ import annotations

import unittest

from dayz_serverman.bridge.contracts import ensure_json_value
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.repositories.mission_map_layout import association_folder, history_name
from tests.mission_map_adoption_support import MOMENT, AdoptionFixture
from tests.test_mission_map_preview import tree

# Top-level fields of the adoption answer
ANSWER_FIELDS = {"version", "profile", "settings_revision", "mission", "case", "route", "records_only", "source",
                 "refused", "refusals", "notice", "happened", "other_profiles_text", "intro", "records", "unchanged",
                 "success", "other_profiles", "retire", "existing_folder", "history_name", "ledger", "plan", "capture",
                 "inputs", "fingerprint", "kept_original"}


class MoveTests(AdoptionFixture):
    """A true move: the old mission folder is gone and no other profile resolves to the source key (OP-2)."""

    def test_true_move(self) -> None:
        """The load offers `move`, and the review names the copied original, the records, the ledger and the marker."""
        operation = self.applied()
        source = self.key()
        old = self.move_root()
        loaded = self.load()
        self.assertEqual((loaded["read_only_reason"], loaded["adoption"]), ("mission_path_changed", {"case": "move"}))
        before = (tree(self.root), tree(self.area))
        answer = self.answer()
        ensure_json_value(answer)
        # The review writes nothing
        self.assertEqual((tree(self.root), tree(self.area)), before)
        self.assertEqual(set(answer), ANSWER_FIELDS)
        self.assertEqual((answer["case"], answer["route"], answer["refused"], answer["refusals"]),
                         ("move", "path_change", False, []))
        self.assertFalse(answer["records_only"])
        paths = {"old_path": str(old), "new_path": str(self.mission.resolve())}
        self.assertEqual(answer["source"], {"mission_key": source, "display_path": str(old)})
        self.assertEqual((answer["retire"], answer["ledger"], answer["existing_folder"], answer["history_name"]),
                         ([source], [operation], False, None))
        self.assertEqual(answer["happened"], texts.HAPPENED["move"].format(**paths))
        self.assertEqual(answer["intro"], texts.INTRO["move"].format(**paths))
        self.assertEqual(answer["records"], [
            texts.ROW_ORIGINAL.format(**paths), texts.ROW_RECORDS["move"], texts.ROW_LEDGER.format(count=1),
            texts.ROW_RETIRED.format(old_path=str(old))])
        self.assertEqual(answer["unchanged"], [texts.UNCHANGED_FILES, texts.UNCHANGED_SOURCE.format(**paths),
                                               texts.UNCHANGED_NOTHING_DELETED])
        self.assertEqual(answer["success"], texts.SUCCESS["move"].format(**paths))
        self.assertEqual(answer["success"], f"Adopted. The map plan and the records now belong to {paths['new_path']}.")
        self.assertEqual((answer["notice"], answer["other_profiles_text"], answer["other_profiles"]), (None, None, []))
        self.assertEqual(answer["plan"], {"state": "VALID", "revision": 0})
        self.assertEqual(self.reader.review("main").fingerprint, answer["fingerprint"])


class PathChangeCopyTests(AdoptionFixture):
    """A copy after a path change: the old mission folder still exists (OP-1, OP-2, D-1)."""

    def test_copy_when_the_old_folder_exists(self) -> None:
        """The plan of this profile is the next revision, and the old folder of this profile is retired."""
        self.applied()
        source = self.key()
        old = self.move_root(keep_old=True)
        self.assertEqual(self.load()["adoption"], {"case": "copy"})
        answer = self.answer()
        paths = {"old_path": str(old), "new_path": str(self.mission.resolve())}
        self.assertEqual((answer["case"], answer["route"], answer["refused"]), ("copy", "path_change", False))
        self.assertEqual(answer["happened"], texts.HAPPENED["copy"].format(**paths))
        self.assertTrue(answer["happened"].startswith(f"{old} still exists or another profile uses it"))
        self.assertEqual(answer["notice"], texts.NOTICE_PATH_CHANGE.format(**paths))
        self.assertEqual(answer["intro"], texts.INTRO["copy"].format(**paths))
        # A copy after a path change takes the plan of this profile as the next revision, and the preview says so
        self.assertIn(f"Map plan of this profile: copied to {paths['new_path']} as the next revision.",
                      answer["records"][1])
        self.assertEqual(answer["records"][-1], texts.ROW_RETIRED.format(old_path=str(old)))
        self.assertEqual(answer["success"], texts.SUCCESS["copy"].format(**paths))
        self.assertEqual(answer["retire"], [source])

    def test_an_existing_active_folder_gets_a_history_name(self) -> None:
        """The existing folder of the active key is named first; the dated name does not bind the fingerprint."""
        self.applied()
        self.move_root(keep_old=True)
        association_folder(self.area, "main", self.key()).mkdir(parents=True)
        answer = self.answer()
        name = history_name(self.key(), MOMENT)
        self.assertEqual((answer["existing_folder"], answer["history_name"]), (True, name))
        self.assertEqual(answer["records"][0], texts.ROW_EXISTING.format(new_path=str(self.mission.resolve()),
                                                                   history_name=name))
        later = type(self.reader)(self.plans, self.profiles, clock=lambda: MOMENT.replace(hour=13))
        review = later.review("main")
        self.assertNotEqual(review.answer["history_name"], name)
        self.assertEqual(review.fingerprint, answer["fingerprint"])
        self.assertTrue(review.context.existing_folder)


class CaptureCopyTests(AdoptionFixture):
    """A copy after a capture refusal: a fresh profile on a copied mission (UX-2, V-10.2-F2)."""

    def test_copy_after_a_capture_refusal(self) -> None:
        """`capture.adoption` offers the copy; the review takes the matched key and keeps the plan of the folder."""
        operation = self.applied()
        source = self.key()
        copy = self.copy_mission()
        preview = self.preview.preview("third")
        self.assertEqual(preview["capture"]["adoption"], {"case": "copy"})
        # The load of a fresh profile has no record that shows the copy
        self.assertIsNone(self.load("third")["adoption"])
        answer = self.answer("third")
        paths = {"old_path": str(self.mission), "new_path": str(copy.resolve())}
        self.assertEqual((answer["case"], answer["route"], answer["refused"]), ("copy", "capture", False))
        self.assertEqual(answer["source"], {"mission_key": source, "display_path": str(self.mission)})
        self.assertEqual(answer["happened"], texts.HAPPENED[("copy", "capture")].format(**paths))
        self.assertEqual(answer["notice"], texts.NOTICE_CAPTURE.format(**paths))
        self.assertEqual(answer["intro"], texts.INTRO[("copy", "capture")].format(**paths))
        self.assertEqual(answer["records"], [texts.ROW_ORIGINAL.format(**paths),
                                             texts.ROW_RECORDS[("copy", "capture")].format(**paths),
                                             texts.ROW_LEDGER.format(count=1)])
        self.assertEqual(answer["unchanged"], [texts.UNCHANGED_FILES, texts.UNCHANGED_SOURCE.format(**paths),
                                               texts.UNCHANGED_PLAN, texts.UNCHANGED_NOTHING_DELETED])
        self.assertEqual(answer["success"], texts.SUCCESS[("copy", "capture")].format(**paths))
        # Nothing of this profile is retired; the other profiles of the source key are named (ADOPT-13)
        self.assertEqual((answer["retire"], answer["ledger"], answer["plan"]), ([], [operation], None))
        self.assertEqual([item["profile_id"] for item in answer["other_profiles"]], ["main", "second"])
        self.assertEqual(answer["other_profiles_text"], texts.OTHER_PROFILES.format(**paths))
        self.assertEqual(answer["capture"]["match"]["operation_id"], operation)


if __name__ == "__main__":
    unittest.main()
