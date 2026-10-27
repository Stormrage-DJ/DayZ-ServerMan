"""Regress exact polygon crossings, eighth-bit originals and absent encounter areas."""

from __future__ import annotations

import math
import unittest

from dayz_serverman.domain.mission_map_evaluation import evaluate_location
from dayz_serverman.domain.mission_map_terrain import ValueRows
from tests.mission_map_evaluation_support import finish, fixture
from tests.test_mission_map_evaluation_tiers import brute_force
from tests.test_mission_map_plan import circle, encounter, square


class ExactScanlineTests(unittest.TestCase):
    """Compare optimized polygon intervals against the pre-existing per-cell predicates."""

    def test_512_vertex_concave_star_matches_cell_oracle(self) -> None:
        """Many row intersections retain exact even-odd parity without repeated whole-polygon tests."""
        vertices = []
        for index in range(512):
            radius = 500 if index % 2 == 0 else 50
            angle = math.tau * index / 512
            vertices.append({"x": 500 + radius * math.cos(angle), "z": 500 + radius * math.sin(angle)})
        target = {**square("target"), "vertices": vertices, "tier": None}
        for bits in (4, 8):
            # A small grid allows exhaustive geometry checks; the native grid is benchmarked separately.
            plan, capabilities, values = fixture([target], bits=bits, columns=32, rows=32)
            result = finish(plan, capabilities, values, "target")["result"]
            expected = brute_force(plan, capabilities, values, "target")
            self.assertEqual({key: result[key] for key in expected}, expected)

    def test_eighth_bit_zero_and_all_bits_original_values(self) -> None:
        """Distinct original flags preserve Tier8 and zero without truncation."""
        plan, capabilities, values = fixture([circle("target", 500, 500, 900)], bits=8)
        for raw, expected in ((128, ["Tier8"]), (255, list(capabilities.flags)), (0, [])):
            layer = ValueRows(values.header, 0, values.header.rows, bytes([raw]) * 48)
            result = finish(plan, capabilities, layer, "target")["result"]
            self.assertEqual(result["original_tiers"], expected)
            self.assertEqual(result["effective_tiers"], expected)
            self.assertEqual(result["cell_count"], 48)

    def test_near_vertex_and_horizontal_boundaries_match_oracle(self) -> None:
        """Adjacent-float vertices and a narrow notch retain exact inclusive endpoints."""
        for edge_x in (437.5, math.nextafter(437.5, math.inf), math.nextafter(437.5, -math.inf)):
            vertices = [(62.5, 125), (937.5, 125), (937.5, 875), (562.5, 875),
                        (562.5, 375), (edge_x, 375), (edge_x, 875), (62.5, 875)]
            target = {**square("target"), "tier": None, "vertices": [{"x": x, "z": z} for x, z in vertices]}
            plan, capabilities, values = fixture([target], rows=4)
            result = finish(plan, capabilities, values, "target")["result"]
            expected = brute_force(plan, capabilities, values, "target")
            self.assertEqual({key: result[key] for key in expected}, expected)


class AbsentAreaTests(unittest.TestCase):
    """Implement the Product Owner distinction between object areas and point Inspect."""

    def test_radius_less_disabled_loot_has_no_area_even_on_cell_centre(self) -> None:
        """A disabled stored loot radius cannot invent an area; point Inspect remains ordinary."""
        for position in ((62.5, 1000 / 12), (63.5, 1000 / 12 + 1)):
            for radius in (None, 40):
                item = encounter("target", "ai_bandits")
                item["centre"] = {"x": position[0], "z": position[1]}
                item["linked_loot"]["radius"] = radius
                plan, capabilities, values = fixture([item])
                result = finish(plan, capabilities, values, "target")["result"]
                self.assertEqual((result["cell_count"], result["original_tiers"], result["effective_tiers"]), (0, [], []))
                point = evaluate_location(plan, capabilities, values, *position)
                self.assertEqual(point["header_cell"], {"i": 0, "j": 0})
                self.assertIsNotNone(point["cell"])
                self.assertEqual(point["original_tier"], [])

    def test_absent_area_retains_final_freshness_gate(self) -> None:
        """An empty computed result is withheld when either source or plan becomes stale."""
        plan, capabilities, values = fixture([encounter("target", "ai_bandits")])
        calls = []
        def stale():
            """Record the final freshness check and refuse publication of the result."""
            calls.append(True)
            return False
        self.assertEqual(finish(plan, capabilities, values, "target", stale), {"status": "stale"})
        self.assertEqual(calls, [True])

    def test_enabled_excluded_ai_loot_retains_area_sets(self) -> None:
        """Encounter exclusion leaves an enabled D6 linked loot area active."""
        item = encounter("target", "ai_bandits")
        item["excluded"] = True
        item["linked_loot"].update(enabled=True, radius=900, tier="Tier3")
        plan, capabilities, values = fixture([item])
        result = finish(plan, capabilities, values, "target")["result"]
        self.assertEqual(result["cell_count"], 48)
        self.assertEqual(result["effective_tiers"], ["Tier3"])


if __name__ == "__main__":
    unittest.main()
