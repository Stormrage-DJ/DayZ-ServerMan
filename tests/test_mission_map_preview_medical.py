"""Medical base rule, coordinated manager changes and external edits in the apply preview (8.6, F1, A6, R17)."""

from __future__ import annotations

import unittest

from dayz_serverman.repositories.medical_zone_fragments import patch_zones, zones_enabled
from dayz_serverman.repositories.mission_map_infected import MARKER_BEGIN
from tests.mission_map_mapgroup_support import PROTOTYPE, zone
from tests.mission_map_preview_support import PreviewFixture

# A hospital whose usage children differ from both its off and its on form
MIXED = PROTOTYPE.replace(b'        <usage name="Town" />\r\n        <container name="lootFloor" lootmax="3">',
                          b'        <container name="lootFloor" lootmax="3">')


class MedicalBaseTests(PreviewFixture):
    """The base takes the medical state of the current mapgroupproto.xml before the 6.8 builder runs."""

    def setUp(self) -> None:
        """Store the medical baseline and apply a capacity plan while medical is off."""
        super().setUp()
        self.set_medical_baseline()
        self.save([zone("rich")], 25)
        self.apply(self.review())
        self.applied = (self.mission / "mapgroupproto.xml").read_bytes()

    def test_toggle_after_apply_is_coordinated_and_kept_by_the_next_apply(self) -> None:
        """Medical on after Apply is a coordinated change; Apply again keeps it, and a third review changes nothing."""
        self.write("mapgroupproto.xml", patch_zones(self.applied, PROTOTYPE, True))
        review = self.review()
        answer = review.answer
        self.assertEqual(answer["coordinated_changes"], [{"path": "mapgroupproto.xml",
                                                          "feature": "medical_loot_zones", "reference": "applied"}])
        self.assertEqual((answer["external_changes"], answer["choices"]["external_changes"]), ([], []))
        self.assertEqual(answer["base"], "baseline")
        candidate = review.candidates.contents()["mapgroupproto.xml"]
        self.assertTrue(zones_enabled(candidate, PROTOTYPE))
        # The 6.3 patcher left the generated group in the old state; the candidate restores clone parity
        current = (self.mission / "mapgroupproto.xml").read_bytes()
        start = current.index(b'<group name="ServerManMap_G_')
        self.assertEqual(candidate[:start], current[:start])
        self.assertIn(b'<usage name="Town" />', current[start:])
        self.assertNotIn(b'<usage name="Town" />', candidate[start:])
        self.assertEqual([item["path"] for item in answer["affected_files"]], ["mapgroupproto.xml"])
        self.apply(review)
        self.assertTrue(zones_enabled((self.mission / "mapgroupproto.xml").read_bytes(), PROTOTYPE))
        again = self.review().answer
        self.assertEqual((again["coordinated_changes"], again["external_changes"], again["affected_files"]), ([], [], []))

    def test_toggle_with_a_plan_change_carries_the_medical_state(self) -> None:
        """A changed plan after the toggle builds every group from the base in the current state."""
        self.write("mapgroupproto.xml", patch_zones(self.applied, PROTOTYPE, True))
        self.save([zone("very_rich")], 50, expected=0)
        review = self.review()
        candidate = review.candidates.contents()["mapgroupproto.xml"]
        self.assertTrue(zones_enabled(candidate, PROTOTYPE))
        self.assertEqual([item["path"] for item in review.answer["affected_files"]][0], "mapgroupproto.xml")
        self.assertFalse(review.answer["refused"])

    def test_other_edits_stay_external_with_a_medical_baseline(self) -> None:
        """A lootmax edit, also next to a medical toggle, is no coordinated change."""
        edited = self.applied.replace(b'name="Land_Shed" lootmax="', b'name="Land_Shed" lootmax="1')
        for data in (edited, patch_zones(edited, PROTOTYPE, True)):
            with self.subTest(toggled=data != edited):
                self.write("mapgroupproto.xml", data)
                answer = self.review().answer
                self.assertEqual(answer["coordinated_changes"], [])
                self.assertEqual([(item["path"], item["owned_changed"]) for item in answer["external_changes"]],
                                 [("mapgroupproto.xml", True)])

    def test_mixed_state_refuses_with_the_medical_reason(self) -> None:
        """A state that differs from both forms refuses the complete candidate before the 6.8 builder."""
        self.write("mapgroupproto.xml", MIXED)
        answer = self.review().answer
        refusal = next(item for item in answer["refusals"] if item["file"] == "mapgroupproto.xml")
        self.assertEqual(refusal["reason"], "medical_state")
        self.assertIn("differs from both", refusal["message"])
        self.assertTrue(answer["refused"])
        self.assertNotIn("mapgroupproto.xml", [item["path"] for item in answer["affected_files"]])
        self.assertEqual([item["path"] for item in answer["external_changes"]], ["mapgroupproto.xml"])


class ExternalEditTests(PreviewFixture):
    """Edits that no manager writer explains are external edits with a choice (R17)."""

    def setUp(self) -> None:
        """Apply a capacity plan without a medical baseline."""
        super().setUp()
        self.save([zone("rich")], 25)
        self.apply(self.review())
        self.applied = (self.mission / "mapgroupproto.xml").read_bytes()

    def test_edit_of_an_owned_value(self) -> None:
        """A changed lootmax of an ordinary group is an external edit of an editor-owned value."""
        self.write("mapgroupproto.xml", self.applied.replace(b'name="Land_Shed" lootmax="', b'name="Land_Shed" lootmax="1'))
        answer = self.review().answer
        self.assertEqual(answer["choices"]["external_changes"], ["mapgroupproto.xml"])
        change = answer["external_changes"][0]
        self.assertEqual((change["reference"], change["owned_changed"], change["exists_now"]), ("applied", True, True))
        self.assertEqual(answer["coordinated_changes"], [])

    def test_edit_outside_owned_values_and_removed_files(self) -> None:
        """A comment edit keeps the owned values; a removed file and a damaged marker block are external too."""
        self.write("mapgroupproto.xml", self.applied.replace(b"keep this comment", b"admin comment"))
        self.write("mapgrouppos.xml", None)
        territory = (self.mission / "env/zombie_territories.xml").read_bytes()
        self.write("env/zombie_territories.xml", territory + MARKER_BEGIN)
        changes = {item["path"]: item for item in self.review().answer["external_changes"]}
        self.assertEqual(sorted(changes), ["env/zombie_territories.xml", "mapgrouppos.xml", "mapgroupproto.xml"])
        self.assertFalse(changes["mapgroupproto.xml"]["owned_changed"])
        self.assertEqual((changes["mapgrouppos.xml"]["exists_now"], changes["mapgrouppos.xml"]["owned_changed"]),
                         (False, True))
        self.assertTrue(changes["env/zombie_territories.xml"]["owned_changed"])

    def test_without_medical_baseline_a_toggle_form_is_external(self) -> None:
        """Without a stored baseline no medical change can be proved, so the edit stays external."""
        self.write("mapgroupproto.xml", patch_zones(self.applied, PROTOTYPE, True))
        answer = self.review().answer
        self.assertEqual(answer["coordinated_changes"], [])
        self.assertEqual(answer["choices"]["external_changes"], ["mapgroupproto.xml"])

    def test_first_apply_and_shared_original_references(self) -> None:
        """Before any record there is no reference; a new association on a captured target compares with the original."""
        self.save([zone("rich")], identifier="second")
        answer = self.review("second").answer
        self.assertEqual(answer["base"], "original")
        # The files that profile main wrote differ from the shared original in editor-owned values
        changes = {item["path"]: item for item in answer["external_changes"]}
        self.assertEqual(sorted(changes), ["mapgroupdirt.xml", "mapgrouppos.xml", "mapgroupproto.xml"])
        self.assertTrue(all(item["reference"] == "original" and item["owned_changed"] for item in changes.values()))
        self.assertEqual(answer["other_profiles"], [{"profile_id": "main", "display_name": "Main"}])
        self.assertFalse(answer["capture"]["required"])


if __name__ == "__main__":
    unittest.main()
