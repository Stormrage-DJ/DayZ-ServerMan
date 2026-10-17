"""Leaf 10.13: the records-only adoption review and several matching old folders.

A path change is records only in one state. The old folder of this profile has no last-applied record, and its
key has no original. The rule is point 5 of the Architect entry 2026-10-17 09:30:36 in evidence 10.13. The texts
are the Product Owner texts of 2026-10-17 09:31:28 in evidence 10.13.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from dayz_serverman.application.mission_map_plans import plan_store
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.repositories.mission_map_layout import association_folder, history_name
from tests.mission_map_adoption_support import MOMENT, AdoptionFixture
from tests.mission_map_mapgroup_support import zone
from tests.test_mission_map_plan import stored_plan

# The relative mission root of profile main, which every old folder names
MAIN_ROOT = "mpmissions\\dayzOffline.chernarusplus"
# An old association folder of profile main without records other than its plan
OTHER_KEY = "c" * 32


class RecordsOnlyTests(AdoptionFixture):
    """No Apply wrote files at the old path: nothing to prove and no original to copy."""

    def test_records_only_copy(self) -> None:
        """The plan moves as the next revision; no original, baseline or ledger row shows."""
        self.save([zone("rich")])
        self.move_root()
        self.assertEqual(self.load()["adoption"], {"case": "copy"})
        answer = self.answer()
        paths = {"old_path": MAIN_ROOT, "new_path": str(self.mission.resolve())}
        self.assertEqual((answer["case"], answer["route"], answer["records_only"], answer["refused"]),
                         ("copy", "path_change", True, False))
        self.assertEqual(answer["happened"], texts.HAPPENED[("copy", "records_only")].format(**paths))
        self.assertIn("there is no saved original to copy", answer["happened"])
        self.assertEqual(answer["intro"], texts.INTRO[("copy", "records_only")].format(**paths))
        self.assertEqual(answer["records"], [
            f"Map plan of this profile: copied to {paths['new_path']} as the next revision.",
            texts.ROW_RETIRED.format(old_path=MAIN_ROOT)])
        self.assertEqual(answer["unchanged"], [
            texts.UNCHANGED_FILES,
            "No saved original exists yet. The first Apply saves one, after the usual check of the mission files.",
            "The records of other profiles stay unchanged.", texts.UNCHANGED_NOTHING_DELETED])
        self.assertEqual(answer["success"], f"Adopted. The map plan of this profile is copied to {paths['new_path']}.")
        self.assertEqual((answer["notice"], answer["ledger"], answer["inputs"]["files"]), (None, [], {}))
        self.assertTrue(self.reader.review("main").context.records_only)

    def test_records_only_is_a_copy_also_when_the_folder_facts_give_a_move(self) -> None:
        """Every records-only path change is a copy (Architect 10:11:41); the existing-folder row keeps its text."""
        self.save([zone("rich")])
        self.move_root()
        association_folder(self.area, "main", self.key()).mkdir(parents=True)
        with patch("dayz_serverman.application.mission_map_adoption_review.source_facts", return_value=(False, False)):
            answer = self.answer()
        paths = {"old_path": MAIN_ROOT, "new_path": str(self.mission.resolve()),
                 "history_name": history_name(self.key(), MOMENT)}
        self.assertEqual((answer["case"], answer["records_only"]), ("copy", True))
        self.assertEqual(answer["happened"], texts.HAPPENED[("copy", "records_only")].format(**paths))
        self.assertNotIn(("move", "records_only"), texts.HAPPENED)
        self.assertEqual(answer["intro"], "Adoption writes this record for {new_path}. It retires the old folder of "
                                          "records of this profile and keeps it as history.".format(**paths))
        self.assertEqual(answer["records"], [texts.ROW_EXISTING.format(**paths),
                                             texts.ROW_RECORDS[("copy", "records_only")].format(**paths),
                                             texts.ROW_RETIRED.format(old_path=MAIN_ROOT)])
        self.assertEqual(answer["success"], texts.SUCCESS[("copy", "records_only")].format(**paths))
        for row in answer["records"]:
            self.assertFalse(row.startswith(("Saved original", "Working baseline", "Records of earlier")))


class SeveralFoldersTests(AdoptionFixture):
    """Every matching old folder of this profile is retired and has its own row (Architect point 11)."""

    def test_each_matching_folder_has_a_retired_row(self) -> None:
        """The folder with the newest plan is the source; both folders are retired."""
        self.applied()
        source = self.key()
        plan = stored_plan([zone("rich")])
        plan.update(association={"profile_id": "main", "mission_key": OTHER_KEY, "mission_root": MAIN_ROOT,
                                 "runtime_profile_key": None}, background=None)
        plan_store(self.area, "main", OTHER_KEY).save(plan, None, MOMENT)
        older = association_folder(self.area, "main", OTHER_KEY) / "plan.json"
        os.utime(older, ns=(10**9, 10**9))
        old = self.move_root()
        answer = self.answer()
        self.assertEqual((answer["source"]["mission_key"], answer["retire"]), (source, [source, OTHER_KEY]))
        self.assertEqual(answer["records"][-2:], [texts.ROW_RETIRED.format(old_path=str(old)),
                                                  texts.ROW_RETIRED.format(old_path=MAIN_ROOT)])
        self.assertEqual(self.reader.review("main").context.retire_keys, (source, OTHER_KEY))


if __name__ == "__main__":
    unittest.main()
