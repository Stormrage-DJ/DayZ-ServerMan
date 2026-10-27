"""Verify row-span equality to brute force, nibble binding and clock-driven slices."""

from __future__ import annotations

import math
import random
import unittest

from dayz_serverman.application.mission_map_evaluation import ObjectTierRead
from dayz_serverman.domain.mission_map_evaluation_shapes import loot_shape
from dayz_serverman.domain.mission_map_geometry import circle_covers, polygon_covers
from dayz_serverman.domain.mission_map_terrain import tiers_of
from tests.mission_map_evaluation_support import finish, fixture
from tests.test_mission_map_plan import circle, encounter, square


def brute_force(plan, capabilities, values, object_id):
    """Visit individual cell centres using the pre-existing D9 predicates."""
    target = next(item for item in plan["objects"] if item["id"] == object_id)
    original, effective, count = set(), set(), 0
    writable, _ = capabilities.tier_writes(values.header.detected_bounds(), plan["terrain"]["bounds_confirmed"])
    for row in range(values.header.rows):
        for column in range(values.header.columns):
            x, z = values.header.cell_grid().centre(column, row)
            if not covers(target, x, z, target=True):
                continue
            count += 1
            names = tiers_of(values.value(column, row), capabilities.flags)
            original.update(names)
            selected = None
            if writable:
                for item in plan["objects"]:
                    properties = item["linked_loot"] if item["kind"] == "encounter" else item
                    if properties["tier"] is not None and covers(item, x, z):
                        selected = properties["tier"]
                        break
            effective.update(names if selected is None else [selected])
    return {"original_tiers": [name for name in capabilities.flags if name in original],
            "effective_tiers": [name for name in capabilities.flags if name in effective], "cell_count": count}


def covers(item, x, z, *, target=False):
    """Use the original geometry predicates without the optimized span machinery."""
    if item["kind"] == "loot_polygon":
        return polygon_covers([(p["x"], p["z"]) for p in item["vertices"]], x, z)
    radius = item["radius"]
    if item["kind"] == "encounter":
        if not target and not item["linked_loot"]["enabled"]:
            return False
        radius = radius if radius is not None else item["linked_loot"]["radius"]
    return circle_covers(item["centre"]["x"], item["centre"]["z"], radius, x, z)


class SpanParityTests(unittest.TestCase):
    """Compare each result to a direct per-cell geometry and priority oracle."""

    def test_seeded_circle_polygon_overlap_parity_for_four_and_eight_bits(self) -> None:
        """Random circles, angular simple polygons and hidden null winners retain brute-force parity."""
        rng = random.Random(4181)
        for bits in (4, 8):
            for columns in (7, 8):
                for case in range(25):
                    target = circle("target", rng.uniform(0, 1000), rng.uniform(0, 1000), rng.uniform(1, 1000))
                    if case % 2:
                        vertices = [(500 + 400 * math.cos(angle), 500 + 400 * math.sin(angle))
                                    for angle in sorted(rng.uniform(0, math.tau) for _ in range(10))]
                        target = {**square("target"), "vertices": [{"x": x, "z": z} for x, z in vertices], "tier": None}
                    higher = {**circle("higher", rng.uniform(0, 1000), rng.uniform(0, 1000), 300),
                              "tier": "Tier2", "visible": False}
                    lower = {**square("lower", 0, 0, 1000), "tier": "Tier3"}
                    plan, capabilities, values = fixture([higher, target, lower], bits=bits, columns=columns)
                    with self.subTest(bits=bits, columns=columns, case=case):
                        result = finish(plan, capabilities, values, "target")["result"]
                        expected = brute_force(plan, capabilities, values, "target")
                        self.assertEqual({key: result[key] for key in expected}, expected)

    def test_concave_boundaries_tangent_rows_and_mid_byte_spans(self) -> None:
        """A narrow U outline and exact cell-centre edges cannot bridge an uncovered notch."""
        for bits in (4, 8):
            objects = [circle("tangent", 312.5, 250, 125), square("edge", 187.5, 250, 625)]
            outline = [(62.5, 0), (937.5, 0), (937.5, 1000), (562.5, 1000), (562.5, 250),
                       (437.5, 250), (437.5, 1000), (62.5, 1000)]
            objects.append({**square("concave"), "vertices": [{"x": x, "z": z} for x, z in outline]})
            plan, capabilities, values = fixture(objects, bits=bits, rows=4)
            for item in objects:
                result = finish(plan, capabilities, values, item["id"])["result"]
                expected = brute_force(plan, capabilities, values, item["id"])
                self.assertEqual({key: result[key] for key in expected}, expected)

    def test_disabled_linked_loot_and_header_only_sampling(self) -> None:
        """Disabled native encounter areas still report covered originals; no writes means original effective tiers."""
        item = encounter("target")
        item["radius"] = 900
        override = {**circle("upper", 500, 500, 900), "tier": "Tier3"}
        plan, capabilities, values = fixture([override, item], columns=7)
        plan["terrain"]["bounds_confirmed"] = False
        result = finish(plan, capabilities, values, "target")["result"]
        self.assertEqual(result["effective_tiers"], result["original_tiers"])
        self.assertEqual(result["cell_count"], 42)
        self.assertEqual(result["cells"], "header")
        self.assertFalse(result["tier_writable"])

    def test_empty_coverage_and_zero_original_flags(self) -> None:
        """Tiny valid zones with no covered centre return empty sets without failure."""
        plan, capabilities, values = fixture([circle("target", 0, 0, 1)])
        result = finish(plan, capabilities, values, "target")["result"]
        self.assertEqual((result["cell_count"], result["original_tiers"], result["effective_tiers"]), (0, [], []))


class SliceTests(unittest.TestCase):
    """Prove resumable work and fresh-result gating with an injected monotonic clock."""

    def test_injected_clock_returns_within_one_second(self) -> None:
        """Each clock read advances 0.1 seconds, making the half-second target deterministic."""
        plan, capabilities, values = fixture([circle("target", 500, 500, 900)])
        ticks = [0.0]
        def clock():
            """Advance the fake clock at each work budget check."""
            ticks[0] += 0.1
            return ticks[0]
        read = ObjectTierRead(plan, "target", capabilities, values, {"saved": 1}, lambda: True, clock=clock)
        calls = 0
        while True:
            start = ticks[0]
            answer = read.advance()
            self.assertLessEqual(ticks[0] - start, 1.0)
            calls += 1
            if answer["status"] != "running":
                break
        self.assertGreater(calls, 1)
        self.assertEqual(answer["status"], "done")
        self.assertEqual(answer["result"]["cell_count"], 48)

    def test_stale_answer_has_no_tier_data(self) -> None:
        """File or plan freshness failure ends the read without exposing computed tiers."""
        plan, capabilities, values = fixture([circle("target", 500, 500, 900)])
        self.assertEqual(finish(plan, capabilities, values, "target", lambda: False), {"status": "stale"})


if __name__ == "__main__":
    unittest.main()
