"""Verify exact integer transitions and distinct-tier saturation across complex overrides."""

from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from dayz_serverman.application.mission_map_evaluation import ObjectTierRead
from dayz_serverman.domain.mission_map_evaluation_shapes import LootShape
from dayz_serverman.domain.mission_map_evaluation_tiers import TierSampling, distinct_footprints, layer_mask
from dayz_serverman.domain.mission_map_terrain import ValueRows
from tests.mission_map_evaluation_support import finish, fixture
from tests.test_mission_map_evaluation_tiers import brute_force
from tests.test_mission_map_plan import circle, square


def star(identifier: str, x: float, z: float, angle: float, tier: str) -> dict:
    """Build a valid 512-vertex simple star with a translated and rotated footprint."""
    vertices = []
    for index in range(512):
        radius = 400 if index % 2 == 0 else 40
        direction = math.tau * index / 512 + angle
        vertices.append({"x": x + radius * math.cos(direction), "z": z + radius * math.sin(direction)})
    return {**square(identifier), "vertices": vertices, "tier": tier}


class ComplexPriorityTests(unittest.TestCase):
    """Use the original per-cell oracle across repeated, translated and mixed-tier polygons."""

    def test_complex_override_partitions_match_oracle(self) -> None:
        """Geometry pruning preserves priority across different footprints and tier assignments."""
        cases = [
            [star(f"p{i}", 500, 500, 0, "Tier4") for i in range(4)],
            [star(f"p{i}", 450 + i * 30, 450 + i * 20, i * .017, "Tier4") for i in range(4)],
            [star(f"p{i}", 450 + i * 30, 450 + i * 20, i * .017, f"Tier{i+1}") for i in range(4)],
        ]
        for bits in (4, 8):
            for overrides in cases:
                plan, capabilities, values = fixture(overrides + [circle("target", 500, 500, 900)],
                                                      bits=bits, columns=12, rows=12)
                expected = brute_force(plan, capabilities, values, "target")
                result = finish(plan, capabilities, values, "target")["result"]
                self.assertEqual({key: result[key] for key in expected}, expected)

    def test_only_exact_equal_footprints_are_deduplicated(self) -> None:
        """Equal lower geometry is dominated regardless of tier; translated geometry remains."""
        objects = [star("higher", 500, 500, 0, "Tier1"), star("equal", 500, 500, 0, "Tier4"),
                   star("translated", 501, 500, 0, "Tier3"), circle("target", 500, 500, 900)]
        plan, capabilities, values = fixture(objects)
        sampling = TierSampling(plan, "target", capabilities, values, {"saved": 1})
        retained = distinct_footprints(sampling.overrides)
        self.assertEqual([shape.identifier for shape, _ in retained], ["higher", "translated"])


class SaturationTests(unittest.TestCase):
    """Prove effective-set pruning retains originals, coverage and final freshness."""

    def test_saturation_skips_only_further_effective_geometry(self) -> None:
        """A known single effective tier can saturate after the first covered row."""
        plan, capabilities, values = fixture([{**circle("upper", 500, 500, 900), "tier": "Tier1"},
                                             circle("target", 500, 500, 900)], bits=8)
        values = ValueRows(values.header, 0, values.header.rows, bytes([1]) * 48)
        visited = []
        original_spans = LootShape.spans
        def record(shape, grid, row):
            """Record which geometric work executes while preserving the original behavior."""
            visited.append((shape.identifier, row))
            return original_spans(shape, grid, row)
        with patch.object(LootShape, "spans", record):
            result = finish(plan, capabilities, values, "target")["result"]
        self.assertEqual(result["cell_count"], 48)
        self.assertEqual(result["original_tiers"], ["Tier1"])
        self.assertEqual(result["effective_tiers"], ["Tier1"])
        self.assertEqual([row for name, row in visited if name == "upper"], [0])
        self.assertEqual([row for name, row in visited if name == "target"], list(range(6)))

    def test_conservative_outside_original_bit_does_not_skip_needed_work(self) -> None:
        """An original bit outside target overestimates the bound without causing incorrect pruning."""
        objects = [{**circle("upper", 500, 500, 100), "tier": "Tier2"}, circle("target", 500, 500, 200)]
        plan, capabilities, values = fixture(objects, bits=8)
        layer = bytearray([1] * 48)
        layer[-1] = 128
        values = ValueRows(values.header, 0, values.header.rows, bytes(layer))
        expected = brute_force(plan, capabilities, values, "target")
        result = finish(plan, capabilities, values, "target")["result"]
        self.assertEqual({key: result[key] for key in expected}, expected)
        self.assertNotIn("Tier8", result["effective_tiers"])
        self.assertEqual(layer_mask(values), 129)

    def test_four_bit_bound_includes_both_nibbles(self) -> None:
        """High and low cell nibbles both contribute to the conservative original bound."""
        _, _, values = fixture([circle("target", 500, 500, 900)])
        layer = ValueRows(values.header, 0, values.header.rows, b"\x81" * 24)
        self.assertEqual(layer_mask(layer), 9)

    def test_stale_after_saturation_withholds_the_complete_result(self) -> None:
        """Effective-set saturation never bypasses the final freshness gate."""
        plan, capabilities, values = fixture([{**circle("target", 500, 500, 900), "tier": "Tier1"}], bits=8)
        values = ValueRows(values.header, 0, values.header.rows, bytes([1]) * 48)
        read = ObjectTierRead(plan, "target", capabilities, values, {"saved": 1}, lambda: False)
        self.assertEqual(read.advance(), {"status": "stale"})
        self.assertEqual(read.sampling.rows_done, 6)


if __name__ == "__main__":
    unittest.main()
