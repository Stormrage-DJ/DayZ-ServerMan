"""Verify exact native capacity targets: R08 matrix, rounding, ceilings, defaults and no compounding (6.7)."""

from __future__ import annotations

import unittest
from fractions import Fraction

from dayz_serverman.domain.mission_map_capacity_targets import capacity_targets, placement_adjustments
from dayz_serverman.domain.mission_map_evaluation import evaluate_location
from dayz_serverman.domain.mission_map_native_capacity import (
    CEILING_UNRESOLVED, CEILING_UNSUPPORTED, CONTAINER_AMBIGUOUS, BaselineContainer, BaselineSource,
    CapacityRefusal, Ceiling, capacity_factor, capacity_target, half_up,
)
from dayz_serverman.domain.mission_map_values import ABUNDANCE_PERCENT, GLOBAL_ABUNDANCE_STEPS
from tests.mission_map_evaluation_support import fixture
from tests.test_mission_map_plan import circle, encounter


# One placement inside the test zone and one outside it
INSIDE, OUTSIDE = (500.0, 500.0), (950.0, 950.0)


def source(group: int | None, *containers: int | None, points=None, proved: bool = False,
           explicit: bool = True, name: str = "Land_Test") -> BaselineSource:
    """Return a baseline source with explicit (or default) ceilings and optional point counts."""
    counts = points or (None,) * len(containers)
    return BaselineSource(("mapgroupproto.xml", name), Ceiling(group, explicit),
                          tuple(BaselineContainer(f"c{index}", Ceiling(value, explicit), count)
                                for index, (value, count) in enumerate(zip(containers, counts))), proved)


def realized(target) -> tuple[int, list[int]]:
    """Return the realized group and container ceilings of a target."""
    return target.group.realized, [item.realized for item in target.containers]


class RoundingTests(unittest.TestCase):
    """Exact nearest-half-up rounding and the quarter-step factors (A127, A130)."""

    def test_half_up_matches_nearest_with_upward_ties(self) -> None:
        """Every baseline 0 to 200 at every factor rounds to the nearest integer, ties upward."""
        factors = {capacity_factor(g + z) for g in GLOBAL_ABUNDANCE_STEPS for z in ABUNDANCE_PERCENT.values()}
        self.assertEqual(factors, {Fraction(index, 4) for index in range(1, 13)})
        for factor in factors:
            for value in range(201):
                target = value * factor
                lower = target.numerator // target.denominator
                expected = lower + 1 if target - lower >= Fraction(1, 2) else lower
                self.assertEqual(half_up(target), expected, (value, factor))
                self.assertLessEqual(abs(half_up(target) - target), Fraction(1, 2))
        for target, expected in ((Fraction(1, 2), 1), (Fraction(3, 2), 2), (Fraction(5, 2), 3),
                                 (Fraction(9, 2), 5), (Fraction(1, 4), 0), (Fraction(3, 4), 1), (Fraction(0), 0)):
            self.assertEqual(half_up(target), expected)
        with self.assertRaises(ValueError):
            half_up(Fraction(-1, 2))

    def test_factor_accepts_only_allowed_steps(self) -> None:
        """Effective adjustments outside -75 to +200 or off the 25 percent step are refused."""
        self.assertEqual(capacity_factor(-75), Fraction(1, 4))
        self.assertEqual(capacity_factor(200), Fraction(3))
        for invalid in (-100, 225, 10, True, 50.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                capacity_factor(invalid)

    def test_product_owner_examples(self) -> None:
        """Zero stays zero; small changes can stay unchanged; one-quarter becomes zero; caps stay shared."""
        for adjustment in (-75, -50, 0, 50, 200):
            self.assertEqual(realized(capacity_target(source(0, 0), adjustment)), (0, [0]))
        unchanged = capacity_target(source(1, 1), -25)
        self.assertEqual((realized(unchanged), unchanged.changed), ((1, [1]), False))
        self.assertEqual(unchanged.group.requested, Fraction(3, 4))
        self.assertEqual(unchanged.group.difference, Fraction(1, 4))
        self.assertEqual(realized(capacity_target(source(1, 1), -50)), (1, [1]))
        quarter = capacity_target(source(1, 1), -75)
        self.assertEqual((realized(quarter), quarter.changed), ((0, [0]), True))
        self.assertEqual(realized(capacity_target(source(3), 50))[0], 5)
        # Group three with three containers of one: group five, containers two each, no quota or clamp
        shared = capacity_target(source(3, 1, 1, 1), 50)
        self.assertEqual(realized(shared), (5, [2, 2, 2]))
        self.assertEqual([item.requested for item in shared.containers], [Fraction(3, 2)] * 3)


class MatrixTests(unittest.TestCase):
    """The R08 matrix uses the 4.1 winning zone and the global value at each placement."""

    def test_every_global_step_and_preset_matches_shared_evaluation(self) -> None:
        """The adjustment at each placement equals Inspect's effective value; the target follows it."""
        plan, capabilities, values = fixture([circle("zone", 500, 500, 100)])
        for global_value in GLOBAL_ABUNDANCE_STEPS:
            for preset, zone in ABUNDANCE_PERCENT.items():
                with self.subTest(global_value=global_value, preset=preset):
                    plan["global_abundance"], plan["objects"][0]["abundance"] = global_value, preset
                    adjustments = placement_adjustments(plan, (INSIDE, OUTSIDE))
                    shared = tuple(evaluate_location(plan, capabilities, values, x, z, sample_cell=False)
                                   ["abundance"]["effective"] for x, z in (INSIDE, OUTSIDE))
                    self.assertEqual(adjustments, (global_value + zone, global_value))
                    self.assertEqual(adjustments, shared)
                    result = capacity_targets(plan, [(source(8, 4), (INSIDE,))])
                    targets = result.sources[0].targets
                    if global_value + zone == 0:
                        self.assertEqual(targets, ())
                    else:
                        expected = half_up(8 * Fraction(100 + global_value + zone, 100))
                        self.assertEqual(targets[0].group.realized, expected)

    def test_published_rows(self) -> None:
        """0/Rich 1.5x, +100/Very Rich 3x, -50/Rich original, -50/Sparse 0.25x."""
        plan, _, _ = fixture([circle("zone", 500, 500, 100)])
        for global_value, preset, group in ((0, "rich", 6), (100, "very_rich", 12), (-50, "rich", None),
                                            (-50, "sparse", 1)):
            plan["global_abundance"], plan["objects"][0]["abundance"] = global_value, preset
            targets = capacity_targets(plan, [(source(4, 2), (INSIDE,))]).sources[0].targets
            self.assertEqual(targets[0].group.realized if targets else None, group)

    def test_highest_zone_hidden_zone_and_disabled_linked_loot(self) -> None:
        """Only the first covering zone counts; hidden zones count; disabled linked loot does not."""
        upper = {**circle("upper", 500, 500, 50), "visible": False, "locked": True, "abundance": "sparse"}
        lower = {**circle("lower", 500, 500, 300), "abundance": "very_rich"}
        linked = {**encounter("e1"), "centre": {"x": 900.0, "z": 900.0}}
        plan, _, _ = fixture([linked, upper, lower])
        points = ((500.0, 500.0), (700.0, 500.0), (900.0, 900.0))
        self.assertEqual(placement_adjustments(plan, points), (-25, 100, 0))
        plan["objects"][0]["linked_loot"]["enabled"] = True
        self.assertEqual(placement_adjustments(plan, points), (-25, 100, 50))


class CeilingTests(unittest.TestCase):
    """Point bounds, the supported limit, defaults and refusals."""

    def test_point_bound_is_reported_beside_requested_and_realized(self) -> None:
        """Achievable is min(group, sum(min(container, points))); unproved points qualify it."""
        target = capacity_target(source(10, 4, 4, points=(3, 8)), 100)
        self.assertEqual(realized(target), (20, [8, 8]))
        self.assertEqual((target.achievable, target.qualified), (11, True))
        proved = capacity_target(source(10, 4, 4, points=(3, 8), proved=True), 100)
        self.assertEqual((proved.achievable, proved.qualified), (11, False))
        self.assertEqual(capacity_target(source(4, 4, 4, points=(30, 30)), 0).achievable, 4)
        self.assertIsNone(capacity_target(source(10, 4, 4, points=(3, None)), 100).achievable)
        with self.assertRaises(ValueError):
            BaselineContainer("c0", Ceiling(1, True), -1)

    def test_supported_limit_refuses_and_no_universal_limit(self) -> None:
        """A realized value above a proved limit refuses; without one, 200 x 3 is 600."""
        self.assertEqual(realized(capacity_target(source(200, 200), 200)), (600, [600]))
        self.assertEqual(realized(capacity_target(source(10, 4), 200, maximum=30)), (30, [12]))
        with self.assertRaises(CapacityRefusal) as caught:
            capacity_target(source(10, 11), 200, maximum=30)
        self.assertEqual((caught.exception.field, caught.exception.reason), ("c0", CEILING_UNSUPPORTED))

    def test_defaults_scale_like_explicit_values_and_keep_their_origin(self) -> None:
        """A proved default is a baseline value; the explicit flag passes through unchanged."""
        target = capacity_target(source(6, 4, explicit=False), 50)
        self.assertEqual(realized(target), (9, [6]))
        self.assertEqual((target.group.explicit, target.containers[0].explicit), (False, False))
        self.assertEqual(target.group.baseline, 6)
        for invalid in (-1, True, 1.5, "3"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Ceiling(invalid, True)

    def test_unresolved_ceiling_refuses_only_required_targets(self) -> None:
        """A neutral placement needs no proof; an affected one names the field, reason and placements."""
        plan, _, _ = fixture([circle("zone", 500, 500, 100)])
        unresolved = BaselineSource(("mapgroupproto.xml", "Land_Dup"), Ceiling(5, True),
                                    (BaselineContainer("c0", Ceiling(None, False, "default_duplicate")),))
        missing = BaselineSource(("mapgroupproto.xml", "Land_Gap"), Ceiling(None, False), ())
        result = capacity_targets(plan, [(unresolved, (OUTSIDE, INSIDE, INSIDE)), (missing, (OUTSIDE,))])
        self.assertEqual(result.sources[0].adjustments, (0, 50, 50))
        self.assertEqual(result.sources[0].targets, ())
        self.assertEqual([(item.source[1], item.field, item.reason, item.placements) for item in result.refusals],
                         [("Land_Dup", "c0", "default_duplicate", (1, 2))])
        result = capacity_targets(plan, [(missing, (INSIDE,))])
        self.assertEqual([(item.field, item.reason) for item in result.refusals], [(None, CEILING_UNRESOLVED)])
        duplicate = BaselineSource(("mapgroupproto.xml", "Land_Two"), Ceiling(5, True),
                                   (BaselineContainer("a", Ceiling(1, True)), BaselineContainer("a", Ceiling(2, True))))
        with self.assertRaises(CapacityRefusal) as caught:
            capacity_target(duplicate, 50)
        self.assertEqual((caught.exception.field, caught.exception.reason), ("a", CONTAINER_AMBIGUOUS))


class BaselineTests(unittest.TestCase):
    """Targets start from the working baseline: reapply and zone removal never compound."""

    def test_reapply_and_zone_removal_restore_the_baseline(self) -> None:
        """The same plan gives equal targets; removal leaves no target; a restored zone repeats them."""
        plan, _, _ = fixture([circle("zone", 500, 500, 100)])
        plan["global_abundance"] = 50
        baseline = [(source(3, 1, 1, 1), (INSIDE, OUTSIDE, INSIDE))]
        first = capacity_targets(plan, baseline)
        self.assertEqual(capacity_targets(plan, baseline), first)
        targets = first.sources[0].targets
        self.assertEqual([(item.adjustment, realized(item)) for item in targets],
                         [(50, (5, [2, 2, 2])), (100, (6, [2, 2, 2]))])
        # Feeding a realized value back would compound; the baseline input never does
        compounded = capacity_target(source(5, 2, 2, 2), 100)
        self.assertNotEqual(realized(compounded), realized(targets[1]))
        zone = plan["objects"].pop()
        plan["global_abundance"] = 0
        self.assertEqual(capacity_targets(plan, baseline).sources[0].targets, ())
        plan["objects"].append(zone)
        plan["global_abundance"] = 50
        self.assertEqual(capacity_targets(plan, baseline), first)


if __name__ == "__main__":
    unittest.main()
