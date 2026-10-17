"""Leaf 10.13: the load field `adoption` for each path state, `capture.adoption`, and the retired and restore cases."""

from __future__ import annotations

import unittest

from dayz_serverman.domain import mission_map_adoption as rules
from dayz_serverman.domain import mission_map_adoption_texts as texts
from dayz_serverman.repositories.mission_map_layout import association_folder, history_name
from tests.mission_map_adoption_support import MOMENT, AdoptionFixture
from tests.mission_map_mapgroup_support import PROTOTYPE, zone

# The relative mission root of profile main, which every folder of the path change names
MAIN_ROOT = "mpmissions\\dayzOffline.chernarusplus"


class CaseRuleTests(unittest.TestCase):
    """The pure rules of the case choice and of both offer fields (OP-2, UX-2, V-10.2-F2)."""

    def test_path_change_case(self) -> None:
        """Only a gone folder that no other profile resolves to is a move; every other case is a copy."""
        self.assertEqual([rules.path_change_case(exists, shared) for exists, shared in
                          ((False, False), (True, False), (False, True), (True, True))],
                         ["move", "copy", "copy", "copy"])

    def test_load_case_for_each_path_state(self) -> None:
        """The path reasons come first; an invalidated record gives restore; anything else offers nothing."""
        self.assertEqual(rules.load_case("returned_to_earlier_path", True), "retired")
        self.assertEqual(rules.load_case("mission_path_changed", True, old_folder_exists=False, shared=False), "move")
        self.assertEqual(rules.load_case("mission_path_changed", False), "copy")
        self.assertEqual(rules.load_case(None, True), "restore")
        self.assertIsNone(rules.load_case(None, False))
        self.assertEqual([rules.offer(case) for case in (None, "copy")], [None, {"case": "copy"}])
        self.assertRaises(ValueError, rules.offer, "merge")

    def test_capture_case_only_for_the_copy_cause(self) -> None:
        """Only an editor_output refusal with an original of the source key offers the copy."""
        cases = [({"result": "refused", "reason": "editor_output", "adoption_required": True}, "copy"),
                 ({"result": "refused", "reason": "editor_output", "adoption_required": False}, None),
                 ({"result": "refused", "reason": "reserved_conflict", "adoption_required": False}, None),
                 ({"result": "passed", "reason": None, "adoption_required": False}, None),
                 ({"result": "not_required", "reason": None, "adoption_required": False}, None)]
        self.assertEqual([rules.capture_case(capture) for capture, _case in cases], [case for _capture, case in cases])

    def test_texts_have_both_route_variants(self) -> None:
        """ADOPT-12 and ADOPT-7 have a path-change and a capture text (Product Owner 15:30:03 in evidence 10.2)."""
        self.assertNotEqual(texts.variant(texts.HAPPENED, "copy", "capture"),
                            texts.variant(texts.HAPPENED, "copy", "path_change"))
        self.assertEqual(texts.variant(texts.HAPPENED, "move", "path_change"), texts.HAPPENED["move"])
        self.assertIn("which still exists or which another profile uses", texts.NOTICE_PATH_CHANGE)
        self.assertNotIn("another profile", texts.NOTICE_CAPTURE)


class CaptureOfferTests(AdoptionFixture):
    """`capture.adoption` is set only for the copy cause; the fingerprint binds it through the answer."""

    def test_no_offer_for_the_other_capture_results(self) -> None:
        """A passed check, a target with an original and a reserved-name refusal offer nothing."""
        self.save([zone("rich")])
        self.assertEqual(self.preview.preview("main")["capture"]["adoption"], None)
        self.write("mapgroupproto.xml", PROTOTYPE.replace(b"Land_Unplaced", b"ServerManMap_G_0123456789ab"))
        capture = self.preview.preview("main")["capture"]
        self.assertEqual((capture["reason"], capture["adoption"]), ("reserved_conflict", None))
        self.write("mapgroupproto.xml", PROTOTYPE)
        self.save([zone("rich")], 25, expected=0)
        self.apply(self.review())
        self.assertEqual(self.preview.preview("main")["capture"]["result"], "not_required")
        self.assertIsNone(self.preview.preview("main")["capture"]["adoption"])


class LoadOfferTests(AdoptionFixture):
    """The load field for each path state, from the records, the folders and the path state alone."""

    def test_no_offer_without_a_path_change_or_an_invalidated_record(self) -> None:
        """A missing plan, a valid plan and invalid saved data offer nothing."""
        self.assertIsNone(self.load()["adoption"])
        self.save([zone("rich")])
        self.assertIsNone(self.load()["adoption"])
        (association_folder(self.area, "main", self.key()) / "plan.json").write_text("{", encoding="utf-8")
        loaded = self.load()
        self.assertEqual((loaded["read_only_reason"], loaded["adoption"]), ("invalid_saved_data", None))

    def test_restore_case(self) -> None:
        """An invalidated record offers restore while the plan stays editable, also beside invalid saved data."""
        self.save([zone("rich")])
        self.invalidate()
        loaded = self.load()
        self.assertEqual((loaded["read_only"], loaded["read_only_reason"], loaded["adoption"]),
                         (False, "adoption_required", {"case": "restore"}))
        answer = self.answer()
        self.assertEqual((answer["case"], answer["route"], answer["refused"], answer["source"]),
                         ("restore", None, False, None))
        self.assertEqual((answer["happened"], answer["intro"], answer["records"], answer["success"]),
                         (texts.HAPPENED["restore"], texts.INTRO["restore"], [texts.ROW_INVALIDATED],
                          "Adopted. Apply no longer needs adoption for this mission."))
        self.assertEqual(answer["unchanged"], [texts.UNCHANGED_FILES, texts.UNCHANGED_NOTHING_DELETED])
        (association_folder(self.area, "main", self.key()) / "plan.json").write_text("{", encoding="utf-8")
        loaded = self.load()
        self.assertEqual((loaded["read_only_reason"], loaded["adoption"]), ("invalid_saved_data", {"case": "restore"}))

    def test_retired_case(self) -> None:
        """A return to a retired path offers `retired`; the review names the history folder and the chain end."""
        self.save([zone("rich")])
        earlier = self.key()
        self.move_root()
        later = self.key()
        self.retire("main", earlier, later)
        self.save([zone("rich")])
        self.move_root(name="DayZServer")
        self.assertEqual(self.key(), earlier)
        loaded = self.load()
        self.assertEqual((loaded["read_only_reason"], loaded["adoption"]),
                         ("returned_to_earlier_path", {"case": "retired"}))
        answer = self.answer()
        name = history_name(earlier, MOMENT)
        self.assertEqual((answer["case"], answer["route"], answer["refused"]), ("retired", None, False))
        self.assertEqual((answer["retire"], answer["history_name"], answer["source"]["mission_key"]),
                         ([later], name, later))
        paths = {"new_path": str(self.mission.resolve()), "other_path": MAIN_ROOT}
        # Neither key has an original, so step 4 has no row (Product Owner 09:29:14 in evidence 10.13)
        self.assertEqual(answer["records"], [texts.ROW_HISTORY.format(history_name=name),
                                             texts.ROW_RETIRED.format(old_path=MAIN_ROOT),
                                             texts.ROW_FRESH.format(**paths)])
        self.assertEqual(answer["happened"], texts.HAPPENED["retired"].format(
            new_path=str(self.mission.resolve()), other_path=MAIN_ROOT))
        self.assertEqual(answer["intro"], texts.INTRO["retired"])
        self.assertEqual(answer["inputs"]["files"], {})


if __name__ == "__main__":
    unittest.main()
