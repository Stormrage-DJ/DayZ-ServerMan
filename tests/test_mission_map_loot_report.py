"""Verify the zone and capacity classes of the 6.9 loot candidate report."""

from __future__ import annotations

import json
import unittest
from fractions import Fraction

from dayz_serverman.domain.mission_map_evaluation_shapes import loot_shape
from dayz_serverman.domain.mission_map_loot_report_texts import BOUND_TEXTS, CAPACITY_EXPLANATION
from dayz_serverman.domain.mission_map_native_capacity import (
    BaselineContainer, BaselineSource, Ceiling, capacity_target,
)
from dayz_serverman.repositories.mission_map_loot_report_capacity import capacity_entry
from dayz_serverman.repositories.mission_map_loot_report_facts import GroupFacts
from dayz_serverman.repositories.mission_map_mapgroup_read import DIRT, POS, PROTO
from dayz_serverman.repositories.mission_map_terrain import AREAFLAGS_FILE
from tests.mission_map_loot_report_support import CAPABILITIES, entries, loot, report
from tests.mission_map_mapgroup_support import build, plan, zone


class ZoneTests(unittest.TestCase):
    """Each zone lists what the candidate adds, changes and removes, its tier, its preset and its files."""

    def test_zone_lists_additions_changes_and_removals(self) -> None:
        """A Rich zone detaches the hospital and the shed; an existing generated group is unchanged."""
        value = plan(0, zone("rich"))
        names = {clone.source[1]: clone.name for clone in build(value).clones}
        result = report(value, current_groups=[names["Land_Shed"]])
        [entry] = result["zones"]
        self.assertEqual((entry["abundance"], entry["zone_adjustment"], entry["effective_adjustment"]),
                         ("rich", 50, 50))
        self.assertEqual(([item["name"] for item in entry["added"]["groups"]], entry["added"]["placements"]),
                         ([names["Land_City_Hospital"]], 2))
        self.assertEqual([item["name"] for item in entry["unchanged"]["groups"]], [names["Land_Shed"]])
        self.assertEqual(entry["removed"], {"placements": 2})
        self.assertEqual(entry["changed"], {"tier_cells": None})
        self.assertEqual(entry["kept"], {"planner_content": 1})
        self.assertEqual(entry["files"], [PROTO, POS, DIRT])
        self.assertTrue(entry["abundance_only"])
        self.assertEqual(result["abundance_only"], ["zone"])
        self.assertEqual(result["removed_groups"], [])
        moved = report(plan(-25, zone("rich")))["zones"][0]
        self.assertEqual((moved["zone_adjustment"], moved["effective_adjustment"]), (50, 25))

    def test_tier_cells_follow_the_highest_tier_override(self) -> None:
        """Cells go to the highest explicit tier; an inherit zone above them claims none and writes no tier."""
        value = plan(0, loot("top", "normal", None, radius=400.0), loot("high", "normal", "Tier2", radius=300.0),
                     loot("low", "normal", "Tier3", 400.0, 400.0, 400.0))
        result = report(value)
        shapes = [loot_shape(item) for item in value["objects"]]
        grid = CAPABILITIES.header.cell_grid()
        expected = {"high": 0, "low": 0}
        for j in range(grid.rows):
            for i in range(grid.columns):
                tiered = [shape for shape in shapes if shape.tier is not None and shape.covers(*grid.centre(i, j))]
                if tiered:
                    expected[tiered[0].identifier] += 1
        cells = {entry["id"]: entry["changed"]["tier_cells"] for entry in result["zones"]}
        self.assertEqual(cells, {"top": None, **expected})
        self.assertTrue(expected["high"] and expected["low"])
        files = {entry["id"]: entry["files"] for entry in result["zones"]}
        self.assertEqual(files, {"top": [], "high": [AREAFLAGS_FILE], "low": [AREAFLAGS_FILE]})
        self.assertEqual([(item["path"], item["changed"]) for item in result["files"]],
                         [(AREAFLAGS_FILE, True), (PROTO, False), (POS, False), (DIRT, False)])

    def test_removed_groups_are_those_the_candidate_no_longer_has(self) -> None:
        """Generated groups of the mission that the candidate drops are removals; a neutral plan drops all."""
        value = plan(0, zone("rich"))
        kept_name = build(value).clones[0].name
        current = [kept_name, "ServerManMap_G_000000000000"]
        self.assertEqual(report(value, current_groups=current)["removed_groups"], ["ServerManMap_G_000000000000"])
        self.assertEqual(report(plan(0), current_groups=current)["removed_groups"], sorted(current))


class CapacityTests(unittest.TestCase):
    """Requests, realized integers, rounding differences and point bounds stay apart (R08)."""

    def test_rational_request_realized_integer_difference_and_point_bound(self) -> None:
        """Global +50: shed group 3 asks 9/2 and gets 5; each container asks 3/2 and gets 2 with one point."""
        shed = entries(report(plan(50)), "Land_Shed")[50]
        self.assertEqual((shed["class"], shed["mechanism"], shed["factor"], shed["zones"]),
                         ("changed", "ordinary", "3/2", []))
        self.assertEqual({key: shed["group"][key] for key in ("baseline", "requested", "realized", "difference")},
                         {"baseline": 3, "requested": "9/2", "realized": 5, "difference": "1/2"})
        for container in shed["containers"]:
            self.assertEqual((container["requested"], container["realized"], container["difference"]),
                             ("3/2", 2, "1/2"))
            self.assertEqual((container["points"], container["point_bound"], container["point_limited"]),
                             (1, 1, True))
        self.assertEqual((shed["achievable"], shed["qualified"], shed["bound_text"]),
                         (3, True, BOUND_TEXTS["qualified"]))
        house = entries(report(plan(50)), "Land_House")[50]
        self.assertEqual((house["group"]["explicit"], house["group"]["baseline"], house["group"]["realized"]),
                         (False, 6, 9))
        for entry in report(plan(25, zone("very_rich")))["capacity"]:
            for item in [entry["group"], *entry["containers"]]:
                self.assertEqual(Fraction(item["difference"]), item["realized"] - Fraction(item["requested"]))
                self.assertLessEqual(abs(Fraction(item["difference"])), Fraction(1, 2))

    def test_zero_effect_generated_group_is_a_no_change_entry(self) -> None:
        """Global -50 with Rich gives adjustment 0: its generated groups keep the baseline (6.8 QA F6)."""
        value = plan(-50, zone("rich"))
        result = report(value)
        clones = build(value).clones
        self.assertEqual(sorted(entry["generated_group"] for entry in result["capacity"] if entry["generated_group"]),
                         sorted(clone.name for clone in clones))
        hospital = entries(result, "Land_City_Hospital")
        self.assertEqual((hospital[0]["class"], hospital[0]["mechanism"], hospital[0]["zones"]),
                         ("no_change", "generated", ["zone"]))
        self.assertEqual((hospital[0]["group"]["requested"], hospital[0]["group"]["difference"]), ("4", "0"))
        self.assertEqual((hospital[-50]["class"], hospital[-50]["mechanism"]), ("changed", "ordinary"))
        self.assertEqual(result["global"]["changed_groups"], ["Land_City_Hospital", "Land_House", "Land_Shed"])
        # A global value of 0 has no adjustment-0 target: those placements keep the unchanged ordinary group
        self.assertNotIn(0, {target.adjustment for target in build(plan(0, zone("rich"))).targets})

    def test_native_limit_is_reported_and_refuses_above_it(self) -> None:
        """A proved limit is shown; a realized value above it refuses, and the report then has no capacity entry."""
        allowed = report(plan(100), maximum=12)
        self.assertEqual((allowed["native_limit"], allowed["refused"]), (12, False))
        refused = report(plan(100), maximum=4)
        self.assertTrue(refused["refused"])
        self.assertEqual(refused["capacity"], [])
        self.assertIn(("ceiling_unsupported", "Land_Shed"),
                      {(item["reason"], item["source"]) for item in refused["refusals"]})
        self.assertEqual([item["path"] for item in refused["files"]], [AREAFLAGS_FILE])


    def test_point_bound_at_equal_more_and_fewer_points(self) -> None:
        """Equal points do not limit; more points keep the ceiling as the bound; fewer points limit (QA S1, S2)."""
        source = BaselineSource((PROTO, "G"), Ceiling(9, True), tuple(
            BaselineContainer(name, Ceiling(value, True)) for name, value in (("a", 2), ("b", 1), ("c", 4))))
        facts = GroupFacts({"a": (), "b": (), "c": ()}, {"a": 2, "b": 3, "c": 1}, False)
        entry = capacity_entry(capacity_target(source, 0), facts, "ordinary", None, [])
        self.assertEqual([(item["points"], item["point_bound"], item["point_limited"]) for item in entry["containers"]],
                         [(2, 2, False), (3, 1, False), (1, 1, True)])


class PlainDataTests(unittest.TestCase):
    """The report is plain JSON data and promises no live item count."""

    def test_report_is_plain_data_without_live_promises(self) -> None:
        """JSON round trip is exact; no key names live items or a percentage, and the R08 text is exact."""
        result = report(plan(-25, zone("rich"), loot("t", "sparse", "Tier2", 200.0, 200.0, 150.0)),
                        types=[b"<types/>"])
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertEqual(result["explanation"], CAPACITY_EXPLANATION)
        keys: set[str] = set()

        def walk(item: object) -> None:
            """Collect every key of the nested report."""
            if isinstance(item, dict):
                keys.update(key for key in item if isinstance(key, str))
                for child in item.values():
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)
        walk(result)
        self.assertFalse({key for key in keys if any(word in key for word in ("live", "percent", "predict", "items"))})
        self.assertNotIn("%", json.dumps(result).replace(CAPACITY_EXPLANATION, ""))


if __name__ == "__main__":
    unittest.main()
