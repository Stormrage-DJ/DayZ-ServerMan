"""Cover import candidates, Replace and Merge, identifier remapping and the compatibility report (D7)."""

from __future__ import annotations

import copy
import math
import unittest
from typing import Any

from dayz_serverman.domain.mission_map_merge import TransferMode, combine, compatibility, read_candidate
from dayz_serverman.domain.mission_map_plan import PlanForm, parse_plan, to_portable
from dayz_serverman.domain.mission_map_values import PlanValidationError, PlanVersionError
from tests.test_mission_map_plan import circle, encounter, square, stored_plan


def destination(objects: list[dict[str, Any]] | None = None, **changes: Any) -> dict[str, Any]:
    """Return a parsed destination draft on the 1,000 m test terrain."""
    return parse_plan(dict(stored_plan([circle("d1"), circle("D2")] if objects is None else objects), **changes),
                      PlanForm.STORED)


def imported(objects: list[dict[str, Any]] | None = None, **changes: Any) -> dict[str, Any]:
    """Return a stored-form source document, as an export of another manager would hold it after editing."""
    return dict(stored_plan([circle("i1"), square("D1"), encounter("i3")] if objects is None else objects),
                global_abundance=50, **changes)


class CandidateTests(unittest.TestCase):
    """Verify validation of an imported document against the destination."""

    def test_stored_only_fields_and_image_path_are_discarded_and_reported(self) -> None:
        """plan_id, association, records and the local path never enter the draft; copy keeps the path."""
        candidate = read_candidate(imported(), destination())
        self.assertEqual(candidate.discarded, ["association", "plan_id", "records", "background.path"])
        self.assertIsNone(candidate.background["path"])
        kept = read_candidate(imported(), destination(), keep_path=True)
        self.assertEqual(kept.background["path"], "C:\\maps\\map.png")
        self.assertEqual(read_candidate(to_portable(parse_plan(imported(), PlanForm.STORED)), destination()).discarded,
                         [])

    def test_bounds_failures_are_listed_for_exclusion_and_never_clipped(self) -> None:
        """Objects outside the destination bounds, an encounter with its linked loot included, are listed."""
        bandits = dict(encounter("bandits", "ai_bandits"),
                       linked_loot={"enabled": True, "tier": None, "abundance": "rich", "radius": 1500.0})
        candidate = read_candidate(imported([circle("in"), circle("out", x=1200.0), bandits]), destination())
        self.assertEqual([item["id"] for item in candidate.objects], ["in"])
        self.assertEqual([(item["index"], item["id"], item["name"]) for item in candidate.excluded],
                         [(1, "out", "Loot circle 1"), (2, "bandits", "Encounter 1")])
        # The encounter's linked loot is listed with it, as it leaves together with the encounter
        self.assertEqual([item["linked_loot"] for item in candidate.excluded],
                         [None, {"enabled": True, "radius": 1500.0}])
        # The kept object is unchanged: nothing is moved or resized
        self.assertEqual(candidate.objects[0]["centre"], {"x": 100.0, "z": 100.0})

    def test_destination_bounds_decide_not_the_source_bounds(self) -> None:
        """An object outside its own small terrain but inside the destination is kept."""
        small = imported([circle("far", x=900.0)])
        small["terrain"] = dict(small["terrain"], bounds={"x_min": 0, "z_min": 0, "width": 500, "height": 500})
        self.assertEqual(len(read_candidate(small, destination()).objects), 1)

    def test_bounds_independent_failures_refuse_everything(self) -> None:
        """A failure that does not depend on the bounds refuses the whole document and lists each object."""
        bow_tie = dict(square("bow"), vertices=[{"x": 0.0, "z": 0.0}, {"x": 10.0, "z": 10.0},
                                                 {"x": 10.0, "z": 0.0}, {"x": 0.0, "z": 10.0}])
        with self.assertRaises(PlanValidationError) as caught:
            read_candidate(imported([circle("ok"), bow_tie, circle("out", x=2000.0), dict(circle("x"), colour=1)]),
                           destination())
        self.assertEqual([(problem.object_index, problem.bounds_dependent) for problem in caught.exception.problems],
                         [(1, False), (2, True), (3, False)])
        self.assertRaises(PlanVersionError, read_candidate, dict(imported(), version=2), destination())
        self.assertRaises(PlanValidationError, read_candidate, {"schemaVersion": 2, "lootOverrides": []},
                          destination())
        self.assertRaises(PlanValidationError, read_candidate, dict(imported(), objects={"a": 1}), destination())


class CombineTests(unittest.TestCase):
    """Verify Replace and Merge of D7."""

    def test_replace(self) -> None:
        """The imported list and global value replace; association, plan ID, records and terrain stay."""
        target = destination(background=None)
        result, remapped = combine(target, read_candidate(imported(), target), TransferMode.REPLACE)
        self.assertEqual([item["id"] for item in result["objects"]], ["i1", "D1", "i3"])
        self.assertEqual((result["global_abundance"], remapped), (50, []))
        for name in ("association", "plan_id", "records", "terrain"):
            self.assertEqual(result[name], target[name])
        # A destination without a background keeps the imported reference without a path, as a missing image
        self.assertEqual((result["background"]["file_name"], result["background"]["path"]), ("map.png", None))
        own = destination()
        self.assertEqual(combine(own, read_candidate(imported(), own), TransferMode.REPLACE)[0]["background"],
                         own["background"])

    def test_merge_inserts_on_top_in_order_and_remaps_colliding_identifiers(self) -> None:
        """Imported objects enter at index 0 in their order; an identifier that matches without case is renewed."""
        target = destination()
        result, remapped = combine(target, read_candidate(imported(), target), TransferMode.MERGE)
        self.assertEqual([item["name"] for item in result["objects"][:3]],
                         ["Loot circle 1", "Loot polygon 1", "Encounter 1"])
        self.assertEqual([item["id"] for item in result["objects"][3:]], ["d1", "D2"])
        self.assertEqual([entry["from"] for entry in remapped], ["D1"])
        self.assertEqual(result["objects"][1]["id"], remapped[0]["to"])
        self.assertRegex(remapped[0]["to"], "^[0-9a-f]{16}$")
        self.assertEqual(result["global_abundance"], target["global_abundance"])
        self.assertEqual(parse_plan(copy.deepcopy(result), PlanForm.STORED), result)


class CompatibilityTests(unittest.TestCase):
    """Verify the preview contents and the confirmation and refusal rules."""

    def test_same_terrain_needs_no_review(self) -> None:
        """Terrain identities match without case; equal bounds need no placement review."""
        target = destination(terrain=dict(stored_plan()["terrain"], terrain_id="TEST.Terrain"))
        report = compatibility(target, read_candidate(imported(), target), TransferMode.MERGE)
        self.assertEqual((report["terrain_match"], report["bounds_equal"], report["placement_review"]),
                         (True, True, False))
        self.assertEqual((report["insertion"], report["object_count"], report["result_object_count"]), ("top", 3, 5))
        self.assertEqual(report["global_abundance"], {"destination": 0, "imported": 50, "result": 0})
        self.assertFalse(report["confirmation_required"])
        self.assertIsNone(report["refusal"])
        # A preview names the identifier to renew but no provisional new identifier
        self.assertEqual(report["remapped"], [{"from": "D1"}])

    def test_other_terrain_bounds_and_exclusions_need_confirmation(self) -> None:
        """A different terrain or bounds, and any excluded object, need explicit confirmation."""
        target = destination()
        other = imported(terrain=dict(stored_plan()["terrain"], terrain_id="enoch"))
        self.assertTrue(compatibility(target, read_candidate(other, target), TransferMode.REPLACE)["placement_review"])
        wider = imported()
        wider["terrain"] = dict(wider["terrain"], bounds={"x_min": 0, "z_min": 0, "width": 1200, "height": 1000})
        report = compatibility(target, read_candidate(wider, target), TransferMode.REPLACE)
        self.assertEqual(report["bounds_difference"], {"x_min": 0.0, "z_min": 0.0, "width": 200.0, "height": 0.0})
        excluded = compatibility(target, read_candidate(imported([circle("out", x=1500.0)]), target),
                                 TransferMode.MERGE)
        self.assertEqual((excluded["placement_review"], excluded["excluded_count"], excluded["confirmation_required"]),
                         (False, 1, True))

    def test_plan_level_limits_refuse_the_merge(self) -> None:
        """A Merge result above 2,000 objects or 50,000 vertices is refused with the counts."""
        target = destination([circle(f"d{index}") for index in range(1500)])
        report = compatibility(target, read_candidate(imported([circle(f"i{index}") for index in range(600)]), target),
                               TransferMode.MERGE)
        self.assertIn("2100 objects", report["refusal"])
        # 60 destination and 40 imported polygons of 512 vertices hold 51,200 vertices
        angles = [2 * math.pi * index / 512 for index in range(512)]
        ring = [{"x": 500 + 400 * math.cos(angle), "z": 500 + 400 * math.sin(angle)} for angle in angles]
        target = destination([dict(square(f"d{index}"), vertices=ring) for index in range(60)])
        candidate = read_candidate(imported([dict(square(f"i{index}"), vertices=ring) for index in range(40)]), target)
        report = compatibility(target, candidate, TransferMode.MERGE)
        self.assertEqual(report["result_vertex_count"], 51200)
        self.assertIn("51200 polygon vertices", report["refusal"])

    def test_unavailable_objects_are_listed_with_their_reason(self) -> None:
        """The capability check of later tasks names unavailable objects in the preview."""
        target = destination()

        def availability(item: dict[str, Any]) -> str | None:
            """Report every encounter as unsupported."""
            return "the provider is not installed" if item["kind"] == "encounter" else None

        report = compatibility(target, read_candidate(imported(), target), TransferMode.MERGE, availability)
        self.assertEqual(report["unavailable"], [{"id": "i3", "name": "Encounter 1",
                                                  "reason": "the provider is not installed"}])


if __name__ == "__main__":
    unittest.main()
