"""Verify the kept, refusal, unavailable and mechanism classes of the 6.9 loot candidate report."""

from __future__ import annotations

import unittest

from dayz_serverman.domain.mission_map_loot_report_texts import KEPT_TEXTS, MECHANISM_TEXTS, UNAVAILABLE_TEXTS
from dayz_serverman.domain.mission_map_terrain import (
    AREAFLAGS_MISSING, BOUNDS_DIFFER_FROM_HEADER, BOUNDS_NOT_CONFIRMED, LIMITS_INVALID, LIMITS_MISSING,
    MISSION_FILE_UNREADABLE, TERRAIN_FAMILY_UNKNOWN, TIER_HEADER_INVALID, TIER_NOT_HOLDABLE,
)
from dayz_serverman.repositories.mission_map_loot_report_capacity import mechanisms
from dayz_serverman.repositories.mission_map_loot_report_facts import group_facts, supplied_categories
from dayz_serverman.repositories.mission_map_mapgroup_read import POS, PROTO
from dayz_serverman.repositories.mission_map_terrain import AREAFLAGS_FILE
from tests.mission_map_loot_report_support import MISSION, loot, report
from tests.mission_map_mapgroup_support import PLANNER_POS, PROTOTYPE, plan, zone
from tests.test_mission_map_mapgroup_clusters import cluster_file

# Cluster placements: a shed inside the zone (its group is placed elsewhere) and a group placed only here
CLUSTERS = {"mapgroupcluster01.xml": cluster_file(("Land_Shed", "505 0 505"), ("Land_Unplaced", "495 0 495"),
                                                  ("Land_Unplaced", "900 0 900"))}
# Item types: tools and weapons have supply; clothes only with nominal 0; food not at all
TYPES = (b'<types><type name="Hammer"><nominal>5</nominal><category name="tools"/></type>'
         b'<type name="Gun"><nominal> 2 </nominal><category name="weapons"/></type>'
         b'<type name="Shirt"><nominal>0</nominal><category name="clothes"/></type></types>')


class KeptTests(unittest.TestCase):
    """Affected placements that stay unchanged carry their reason, text and winning zone."""

    def test_kept_reasons_have_texts_and_zones(self) -> None:
        """Planner content, a cluster placement and a cluster-only group are reported, not refused."""
        result = report(plan(25, zone("rich")), clusters=CLUSTERS)
        self.assertFalse(result["refused"])
        kept = {(item["file"], item["index"]): item for item in result["kept"]}
        self.assertEqual({key: (item["reason"], item["zone"], item["adjustment"]) for key, item in kept.items()}, {
            (POS, 4): ("planner_content", "zone", 75),
            ("mapgroupcluster01.xml", 0): ("cluster_placement", "zone", 75),
            ("mapgroupcluster01.xml", 1): ("cluster_only_group", "zone", 75),
            ("mapgroupcluster01.xml", 2): ("cluster_only_group", None, 25),
        })
        for item in result["kept"]:
            self.assertEqual(item["text"], KEPT_TEXTS[item["reason"]])
        self.assertEqual(result["zones"][0]["kept"],
                         {"cluster_only_group": 1, "cluster_placement": 1, "planner_content": 1})
        uncontrolled = result["mechanisms"]["uncontrolled_placements"]
        self.assertEqual([(item["reason"], item["count"]) for item in uncontrolled],
                         [("cluster_only_group", 2), ("cluster_placement", 1), ("planner_content", 1)])
        self.assertEqual({item["text"] for item in uncontrolled}, set(KEPT_TEXTS.values()))


class RefusalTests(unittest.TestCase):
    """Source and tier refusals appear apart from the capacity entries and refuse the complete Apply."""

    def test_source_and_tier_refusals(self) -> None:
        """An unknown placement in a zone refuses with its placement; an unholdable tier refuses areaflags.map."""
        unknown = PLANNER_POS.replace(b"PlannerRich_00001", b"Land_Nowhere")
        baseline = dict(MISSION, **{POS: MISSION[POS].replace(PLANNER_POS, PLANNER_POS + unknown)})
        result = report(plan(0, zone("rich"), loot("t", "normal", "Tier8", 100.0, 100.0)), baseline)
        self.assertTrue(result["refused"])
        refusals = {item["reason"]: item for item in result["refusals"]}
        self.assertEqual(set(refusals), {"tier_not_holdable", "source_missing"})
        self.assertEqual((refusals["tier_not_holdable"]["file"], refusals["tier_not_holdable"]["placements"]),
                         (AREAFLAGS_FILE, []))
        self.assertEqual((refusals["source_missing"]["source"], refusals["source_missing"]["placements"]),
                         ("Land_Nowhere", [[POS, 5, "Land_Nowhere"]]))
        self.assertTrue(refusals["source_missing"]["message"].endswith("No file was changed."))
        self.assertEqual((result["capacity"], result["files"]), ([], []))


class UnavailableTests(unittest.TestCase):
    """Unavailable loot objects are listed with their reason and text; abundance-only zones are named."""

    def test_unavailable_objects_and_abundance_only_zones(self) -> None:
        """A tier that the 4-bit layer cannot hold, and an unconfirmed terrain, make loot objects unavailable."""
        result = report(plan(0, loot("t8", "normal", "Tier8"), loot("t2", "rich", "Tier2", 100.0, 100.0),
                             loot("plain", "sparse", None, 800.0, 800.0)))
        self.assertEqual(result["unavailable"], [{"id": "t8", "name": "Loot circle 1", "reason": "tier_not_holdable",
                                                  "text": UNAVAILABLE_TEXTS["tier_not_holdable"]}])
        self.assertEqual(result["abundance_only"], ["plain"])
        unconfirmed = plan(0, loot("t2", "rich", "Tier2"))
        unconfirmed["terrain"]["bounds_confirmed"] = False
        result = report(unconfirmed)
        self.assertEqual([(item["id"], item["reason"]) for item in result["unavailable"]],
                         [("t2", "bounds_not_confirmed")])
        self.assertIn("bounds_not_confirmed", {item["reason"] for item in result["refusals"]})

    def test_every_capability_reason_has_a_text(self) -> None:
        """Each reason that the terrain capabilities can give has its own text (QA F5)."""
        reasons = {TIER_HEADER_INVALID, LIMITS_INVALID, LIMITS_MISSING, AREAFLAGS_MISSING, MISSION_FILE_UNREADABLE,
                   BOUNDS_NOT_CONFIRMED, BOUNDS_DIFFER_FROM_HEADER, TIER_NOT_HOLDABLE, TERRAIN_FAMILY_UNKNOWN}
        self.assertLessEqual(reasons, set(UNAVAILABLE_TEXTS))
        self.assertEqual(UNAVAILABLE_TEXTS[MISSION_FILE_UNREADABLE], "A mission file could not be read.")


class MechanismTests(unittest.TestCase):
    """Fixed dispatch, shared category capacity, missing supply and uncontrolled placements of changed groups."""

    def test_mechanisms_of_the_changed_groups(self) -> None:
        """The shed has fixed dispatch; the hospital shares one cap over three categories; two lack supply."""
        result = report(plan(50), types=[TYPES])["mechanisms"]
        self.assertEqual(result["fixed_dispatch"], {"groups": ["Land_Shed"], "text": MECHANISM_TEXTS["fixed_dispatch"]})
        self.assertEqual(result["shared_category_capacity"]["groups"],
                         [{"source": "Land_City_Hospital", "categories": ["weapons", "tools", "clothes"]}])
        self.assertEqual(result["missing_supply"], {"categories": {"clothes": ["Land_City_Hospital"],
                                                                   "food": ["Land_House"]},
                                                    "text": MECHANISM_TEXTS["missing_supply"]})

    def test_missing_supply_is_unknown_without_readable_types(self) -> None:
        """Without item types, or with a types file that cannot be read, missing supply is unknown, not empty."""
        for types in (None, [], [b"<types"], [TYPES.replace(b"<nominal>5", b"<nominal>x")]):
            result = report(plan(50), types=types)["mechanisms"]["missing_supply"]
            self.assertEqual(result, {"categories": None, "text": MECHANISM_TEXTS["missing_supply_unknown"]})
        self.assertIsNone(supplied_categories(None))

    def test_no_change_entries_and_neutral_plans_add_no_mechanism(self) -> None:
        """Only changed groups count: a neutral plan and a cancelling zone with global 0 report none."""
        for value in (plan(0), plan(0, zone("normal"))):
            result = report(value, types=[TYPES])
            self.assertEqual(result["capacity"], [])
            self.assertEqual(result["mechanisms"]["fixed_dispatch"]["groups"], [])
            self.assertEqual(result["mechanisms"]["missing_supply"]["categories"], {})

    def test_only_changed_entries_count(self) -> None:
        """A no-change entry with dispatch and two unsupplied categories adds nothing (QA S3)."""
        changed = {"class": "changed", "source": "A", "fixed_dispatch": False, "categories": ["tools"]}
        same = {"class": "no_change", "source": "B", "fixed_dispatch": True, "categories": ["x", "y"]}
        result = mechanisms([changed, same], [], [TYPES])
        self.assertEqual((result["fixed_dispatch"]["groups"], result["shared_category_capacity"]["groups"],
                          result["missing_supply"]["categories"]), ([], [], {}))

    def test_group_facts_read_categories_points_and_dispatch(self) -> None:
        """Facts keep exact category spelling and count points per container; an unreadable file has none."""
        facts = group_facts(PROTOTYPE)
        self.assertEqual(facts["Land_City_Hospital"].points, {"lootFloor": 2, "lootShelves": 1})
        self.assertEqual((facts["Land_Shed"].fixed_dispatch, facts["Land_House"].fixed_dispatch), (True, False))
        self.assertEqual(group_facts(b"<prototype"), {})
        self.assertEqual(group_facts(None), {})
        self.assertNotIn(PROTO, facts)


if __name__ == "__main__":
    unittest.main()
