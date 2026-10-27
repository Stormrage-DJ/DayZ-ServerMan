"""Verify shared priority, Inspect, linked loot and additive abundance contracts."""

from __future__ import annotations

import math
import unittest

from dayz_serverman.domain.mission_map_evaluation import evaluate_location, evaluate_plan
from dayz_serverman.domain.mission_map_terrain import TerrainCapabilities
from dayz_serverman.domain.mission_map_values import ABUNDANCE_PERCENT, GLOBAL_ABUNDANCE_STEPS
from tests.mission_map_evaluation_support import fixture
from tests.test_mission_map_plan import circle, encounter, square


class LocationEvaluationTests(unittest.TestCase):
    """Assert independent tier and abundance precedence at shared backend boundaries."""

    def test_hidden_null_tier_winner_retains_lower_explicit_tier(self) -> None:
        """A hidden locked winner supplies abundance; the lower hidden tier supplies tier."""
        upper = {**circle("upper", 500, 500, 900), "visible": False, "locked": True, "abundance": "normal"}
        lower = {**circle("lower", 500, 500, 900), "visible": False, "tier": "tier2", "abundance": "very_rich"}
        plan, capabilities, values = fixture([upper, lower])
        plan["global_abundance"] = 100
        answer = evaluate_location(plan, capabilities, values, 500, 500)
        self.assertEqual(answer["winner"]["id"], "upper")
        self.assertEqual(answer["tier_object"]["id"], "lower")
        self.assertEqual(answer["effective_tier"], ["Tier2"])
        self.assertEqual(answer["abundance"]["effective"], 100)
        self.assertTrue(answer["abundance_only"])
        self.assertEqual([item["id"] for item in answer["hidden_contributors"]], ["upper", "lower"])
        plan["objects"].pop(0)
        self.assertEqual(evaluate_location(plan, capabilities, values, 500, 500)["abundance"]["effective"], 200)

    def test_r08_rows_and_all_allowed_steps(self) -> None:
        """Every preset and slider pair adds once; the four published examples agree."""
        plan, capabilities, values = fixture([circle("zone", 500, 500, 900)])
        for global_value in GLOBAL_ABUNDANCE_STEPS:
            for preset, zone in ABUNDANCE_PERCENT.items():
                with self.subTest(global_value=global_value, preset=preset):
                    plan["global_abundance"] = global_value
                    plan["objects"][0]["abundance"] = preset
                    result = evaluate_location(plan, capabilities, values, 500, 500)
                    self.assertEqual(result["abundance"]["effective"], global_value + zone)
                    self.assertEqual(evaluate_plan(plan, capabilities)["objects"][0]["effective_adjustment"],
                                     global_value + zone)
        for global_value, preset, expected in ((0, "rich", 50), (100, "very_rich", 200),
                                               (-50, "rich", 0), (-50, "sparse", -75)):
            plan["global_abundance"] = global_value
            plan["objects"][0]["abundance"] = preset
            self.assertEqual(evaluate_location(plan, capabilities, values, 500, 500)["abundance"]["effective"], expected)
        plan["objects"][0]["radius"] = 100
        outside = evaluate_location(plan, capabilities, values, 0, 0, sample_cell=False)
        self.assertEqual(outside["abundance"]["zone"], 0)
        self.assertEqual(outside["abundance"]["effective"], -50)

    def test_closed_circle_and_polygon_building_points(self) -> None:
        """Building points use their own coordinates and include edges and tangencies."""
        plan, capabilities, values = fixture([circle("zone", 500, 500, 100)])
        for x, covered in ((600.0, True), (math.nextafter(600.0, math.inf), False),
                           (math.nextafter(600.0, -math.inf), True)):
            result = evaluate_location(plan, capabilities, values, x, 500, sample_cell=False)
            self.assertEqual(result["winner"] is not None, covered)
        plan, capabilities, values = fixture([square("zone", 100, 100, 200)])
        for point, covered in (((100, 200), True), ((100, 100), True), ((200, 200), True), ((99, 200), False)):
            self.assertEqual(evaluate_location(plan, capabilities, values, *point, sample_cell=False)["winner"]
                             is not None, covered)

    def test_inspect_samples_centre_and_original_multi_bits(self) -> None:
        """Inspect uses centre coverage and preserves each original bit in flag order."""
        plan, capabilities, values = fixture([circle("zone", 62.5, 83.33333333333333, 1)])
        answer = evaluate_location(plan, capabilities, values, 0, 0)
        self.assertEqual(answer["cell"]["i"], 0)
        self.assertEqual(answer["cell"]["j"], 0)
        self.assertIsNotNone(answer["winner"])
        self.assertEqual(evaluate_location(plan, capabilities, values, 438, 0)["original_tier"], ["Tier1", "Tier2"])

    def test_unavailable_writes_keep_header_original_and_point_abundance(self) -> None:
        """Shifted or unconfirmed bounds cannot override a readable header cell."""
        zone = {**circle("zone", 500, 500, 1), "tier": "Tier2"}
        for change in ({"bounds_confirmed": False}, {"bounds": {"x_min": 1, "z_min": 0, "width": 1000, "height": 1000}}):
            plan, capabilities, values = fixture([zone])
            plan["terrain"].update(change)
            answer = evaluate_location(plan, capabilities, values, 500, 500)
            self.assertIsNone(answer["cell"])
            self.assertEqual(answer["effective_tier"], answer["original_tier"])
            self.assertEqual(answer["not_writable"]["id"], "zone")
            self.assertEqual(answer["abundance"]["zone"], 50)
            self.assertFalse(evaluate_plan(plan, capabilities)["objects"][0]["available"])

    def test_missing_header_and_unholdable_tier_summary(self) -> None:
        """A missing header gives unknown tiers; unsupported explicit flags remain unavailable."""
        plan, capabilities, values = fixture([{**circle(), "tier": "Tier8"}])
        self.assertEqual(evaluate_plan(plan, capabilities)["objects"][0]["reason"], "tier_not_holdable")
        missing = TerrainCapabilities("test", None, (), "tier_header_invalid")
        answer = evaluate_location(plan, missing, None, 100, 100)
        self.assertIsNone(answer["original_tier"])
        self.assertEqual(answer["tier_source"], "unknown")
        self.assertEqual(answer["abundance"]["zone"], 50)
        plan["objects"][0]["tier"] = None
        self.assertTrue(evaluate_plan(plan, missing)["objects"][0]["available"])

    def test_linked_loot_disabled_and_excluded_provider(self) -> None:
        """Enabled linked loot survives encounter exclusion and uses its own AI loot radius."""
        for provider in ("native_infected", "ai_bandits"):
            item = encounter("zone", provider)
            item["excluded"] = True
            item["linked_loot"].update(enabled=True, tier="Tier3", radius=40 if provider == "ai_bandits" else None)
            plan, capabilities, values = fixture([item])
            answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
            self.assertEqual(answer["effective_tier"], ["Tier3"])
            plan["objects"][0]["linked_loot"]["enabled"] = False
            self.assertIsNone(evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)["winner"])


if __name__ == "__main__":
    unittest.main()
