"""Cover plan objects: kinds, provider properties, open bounded objects, linked loot and bounds rules (D1, D6)."""

from __future__ import annotations

import copy
import unittest
from typing import Any, Callable
from unittest import mock

from dayz_serverman.domain.mission_map_geometry import Bounds
from dayz_serverman.domain.mission_map_objects import (
    automatic_name, new_object_fields, object_bounds_problem, parse_object, parse_open_object,
)
from dayz_serverman.domain import mission_map_plan
from dayz_serverman.domain.mission_map_plan import PlanForm, new_identifier, parse_plan, with_new_object
from dayz_serverman.domain.mission_map_values import ObjectKind, ObjectRuleError, PlanValidationError
from tests.test_mission_map_plan import circle, encounter, square, stored_plan


# A 1,000 m square terrain at the origin
BOUNDS = Bounds(0.0, 0.0, 1000.0, 1000.0)


def nested(depth: int) -> dict[str, Any]:
    """Return an open object whose container depth is exactly depth."""
    value: dict[str, Any] = {"leaf": 1}
    for _level in range(depth - 1):
        value = {"inner": value}
    return value


class ObjectKindTests(unittest.TestCase):
    """Verify the closed field sets and value rules of each kind."""

    def refused(self, raw: object) -> ObjectRuleError:
        """Return the rule error of an object that must fail."""
        with self.assertRaises(ObjectRuleError) as caught:
            parse_object(raw)
        return caught.exception

    def edited(self, base: dict[str, Any], edit: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
        """Return a copy of base after one edit."""
        raw = copy.deepcopy(base)
        edit(raw)
        return raw

    def test_each_kind_and_provider_parses(self) -> None:
        """Circles, polygons and encounters of every provider parse to normalized copies."""
        self.assertEqual(parse_object(circle())["radius"], 50.0)
        self.assertEqual(len(parse_object(square())["vertices"]), 4)
        for provider in ("native_infected", "native_wolf", "native_bear", "ai_bandits"):
            with self.subTest(provider=provider):
                self.assertEqual(parse_object(encounter(provider=provider))["provider"], provider)
        # Integer coordinates become floats, so the fingerprint does not depend on the JSON spelling
        parsed = parse_object(dict(circle(), centre={"x": 1, "z": 2}, radius=3))
        self.assertEqual((parsed["centre"], parsed["radius"]), ({"x": 1.0, "z": 2.0}, 3.0))
        self.assertIsInstance(parsed["radius"], float)

    def test_unknown_missing_and_wrong_fields_are_refused(self) -> None:
        """Unknown and missing fields, bad kinds and bad scalar values fail the bounds-independent rules."""
        edits = [
            lambda raw: raw.update(colour="red"), lambda raw: raw.pop("locked"),
            lambda raw: raw.update(kind="loot_square"), lambda raw: raw.update(kind=["loot_circle"]),
            lambda raw: raw.update(id="has space"), lambda raw: raw.update(id="x" * 65),
            lambda raw: raw.update(visible="yes"), lambda raw: raw["centre"].update(y=1.0),
            lambda raw: raw.update(radius=True), lambda raw: raw.update(radius=0.99),
            lambda raw: raw.update(radius=float("nan")), lambda raw: raw.update(abundance="huge"),
        ]
        for edit in edits:
            error = self.refused(self.edited(circle(), edit))
            self.assertFalse(error.bounds_dependent)
        self.refused("not an object")
        self.refused(self.edited(encounter(), lambda raw: raw.update(provider="native_zombie")))

    def test_names_are_trimmed_and_limited(self) -> None:
        """Names have 1 to 80 characters after trimming, without control characters; duplicates are fine."""
        self.assertEqual(parse_object(dict(circle(), name="  Rich town  "))["name"], "Rich town")
        self.assertEqual(parse_object(dict(circle(), name="n" * 80))["name"], "n" * 80)
        for name in ("   ", "n" * 81, "tab\there", "bell\x07", "", 5):
            with self.subTest(name=name):
                self.refused(dict(circle(), name=name))

    def test_tier_names(self) -> None:
        """A tier is null or a value-flag name: a letter, then letters, digits or _, 32 characters or fewer."""
        for tier in (None, "Tier1", "Unique", "a", "T" + "_" * 31):
            self.assertEqual(parse_object(dict(circle(), tier=tier))["tier"], tier)
        for tier in ("1Tier", "_Tier", "Tier-1", "T" * 33, "", 1):
            with self.subTest(tier=tier):
                self.refused(dict(circle(), tier=tier))

    def test_polygon_vertex_count_and_simplicity(self) -> None:
        """Polygons need 3 to 512 vertices and a simple outline."""
        self.refused(dict(square(), vertices=square()["vertices"][:2]))
        many = [{"x": float(index), "z": float(index % 2)} for index in range(513)]
        self.assertIn("512", str(self.refused(dict(square(), vertices=many))))
        bow_tie = [{"x": 0.0, "z": 0.0}, {"x": 10.0, "z": 10.0}, {"x": 10.0, "z": 0.0}, {"x": 0.0, "z": 10.0}]
        self.assertIn("crosses", str(self.refused(dict(square(), vertices=bow_tie))))


class ProviderTests(unittest.TestCase):
    """Verify the closed infected properties, the open bounded objects and the linked loot rules."""

    def test_infected_properties_are_closed_and_ordered(self) -> None:
        """event_name and four integer counts from 0 to 100, each minimum at most its maximum."""
        base = encounter()
        # An event name need not start with Infected; the capability check decides availability later
        renamed = parse_object(dict(base, properties=dict(base["properties"], event_name="ZmbGroup_PrisonYard_2")))
        self.assertEqual(renamed["properties"]["event_name"], "ZmbGroup_PrisonYard_2")
        bad_properties = [
            dict(base["properties"], extra=1), {"event_name": "InfectedCity", "smin": 1, "smax": 2},
            dict(base["properties"], smin=5, smax=4), dict(base["properties"], dmin=3, dmax=2),
            dict(base["properties"], smax=101), dict(base["properties"], smin=-1),
            dict(base["properties"], smin=1.0), dict(base["properties"], event_name="Infected City"),
            dict(base["properties"], event_name="E" * 65),
        ]
        for properties in bad_properties:
            with self.subTest(properties=properties), self.assertRaises(ObjectRuleError):
                parse_object(dict(base, properties=properties))
        edge = dict(base["properties"], smin=0, smax=100, dmin=100, dmax=100)
        self.assertEqual(parse_object(dict(base, properties=edge))["properties"]["smax"], 100)

    def test_open_object_bounds(self) -> None:
        """Depth 4, 64 keys, 256-character strings and 64-item lists are the limits of an open object."""
        self.assertEqual(parse_open_object(nested(4), "properties"), nested(4))
        self.assertRaises(ObjectRuleError, parse_open_object, nested(5), "properties")
        self.assertEqual(len(parse_open_object({f"k{index}": index for index in range(64)}, "p")), 64)
        self.assertRaises(ObjectRuleError, parse_open_object, {f"k{index}": index for index in range(65)}, "p")
        self.assertEqual(parse_open_object({"s": "x" * 256}, "p"), {"s": "x" * 256})
        self.assertRaises(ObjectRuleError, parse_open_object, {"s": "x" * 257}, "p")
        self.assertEqual(len(parse_open_object({"l": list(range(64))}, "p")["l"]), 64)
        self.assertRaises(ObjectRuleError, parse_open_object, {"l": list(range(65))}, "p")
        # A list counts as one level; three lists below the object reach depth 4, a fourth list does not fit
        self.assertEqual(parse_open_object({"l": [[[1]]]}, "p"), {"l": [[[1]]]})
        self.assertRaises(ObjectRuleError, parse_open_object, {"l": [[[[1]]]]}, "p")
        for bad in ({"bad-key": 1}, {"": 1}, {"k" * 65: 1}, {"n": float("inf")}, {"o": object()}, [1]):
            with self.subTest(bad=bad), self.assertRaises(ObjectRuleError):
                parse_open_object(bad, "p")
        # Every JSON value kind is kept as given
        values = {"n": None, "b": False, "i": 3, "f": 2.5, "s": "text", "l": [1, "a", None], "o": {"x": [True]}}
        self.assertEqual(parse_object(dict(encounter(provider="native_wolf"), properties=values))["properties"], values)

    def test_encounter_radius_follows_the_provider(self) -> None:
        """Native providers need a spawn radius; AI_Bandits has none."""
        self.assertRaises(ObjectRuleError, parse_object, dict(encounter(), radius=None))
        self.assertRaises(ObjectRuleError, parse_object, dict(encounter(provider="native_bear"), radius=None))
        self.assertRaises(ObjectRuleError, parse_object, dict(encounter(provider="ai_bandits"), radius=30.0))
        self.assertIsNone(parse_object(encounter(provider="ai_bandits"))["radius"])

    def test_linked_loot_radius_rule(self) -> None:
        """Native linked loot uses the encounter radius; enabled AI_Bandits linked loot needs its own radius."""
        loot = {"enabled": True, "tier": "Tier3", "abundance": "very_rich", "radius": None}
        self.assertEqual(parse_object(dict(encounter(), linked_loot=loot))["linked_loot"]["tier"], "Tier3")
        self.assertRaises(ObjectRuleError, parse_object, dict(encounter(), linked_loot=dict(loot, radius=20.0)))
        bandits = encounter(provider="ai_bandits")
        self.assertRaises(ObjectRuleError, parse_object, dict(bandits, linked_loot=loot))
        own = parse_object(dict(bandits, linked_loot=dict(loot, radius=75)))
        self.assertEqual(own["linked_loot"]["radius"], 75.0)
        # Disabled linked loot may keep a radius or have none; a kept radius still follows the circle rule
        self.assertIsNone(parse_object(bandits)["linked_loot"]["radius"])
        kept = dict(loot, enabled=False, radius=10.0)
        self.assertEqual(parse_object(dict(bandits, linked_loot=kept))["linked_loot"]["radius"], 10.0)
        self.assertRaises(ObjectRuleError, parse_object, dict(bandits, linked_loot=dict(kept, radius=0.5)))
        self.assertRaises(ObjectRuleError, parse_object, dict(bandits, linked_loot=dict(loot, extra=1)))


class BoundsRuleTests(unittest.TestCase):
    """Verify the bounds-dependent rules that import may resolve by exclusion (D7)."""

    def test_points_must_lie_in_the_closed_bounds(self) -> None:
        """Centres and vertices on the edge pass; outside fails."""
        self.assertIsNone(object_bounds_problem(parse_object(circle(x=0.0, z=1000.0)), BOUNDS))
        self.assertIsNotNone(object_bounds_problem(parse_object(circle(x=-0.5)), BOUNDS))
        self.assertIsNone(object_bounds_problem(parse_object(square(x=980.0, z=980.0)), BOUNDS))
        self.assertIsNotNone(object_bounds_problem(parse_object(square(x=990.0, z=10.0)), BOUNDS))
        moved = dict(encounter(), centre={"x": 500.0, "z": 1000.1})
        self.assertIsNotNone(object_bounds_problem(parse_object(moved), BOUNDS))

    def test_radius_limit_is_the_larger_extent(self) -> None:
        """A circle, an encounter and a linked loot radius may reach the larger extent, not beyond it."""
        wide = Bounds(0.0, 0.0, 1000.0, 400.0)
        self.assertIsNone(object_bounds_problem(parse_object(circle(radius=1000.0, z=200.0)), wide))
        self.assertIsNotNone(object_bounds_problem(parse_object(circle(radius=1000.5, z=200.0)), wide))
        self.assertIsNotNone(object_bounds_problem(parse_object(dict(encounter(), radius=1001.0)), BOUNDS))
        loot = {"enabled": True, "tier": None, "abundance": "rich", "radius": 1200.0}
        bandits = dict(encounter(provider="ai_bandits"), linked_loot=loot)
        self.assertIn("radius", object_bounds_problem(parse_object(bandits), BOUNDS))


class NewObjectTests(unittest.TestCase):
    """Verify automatic names and the defaults of new objects."""

    def test_automatic_names_count_past_the_highest_number(self) -> None:
        """N is 1 more than the highest number that a name of the same pattern uses."""
        objects = [{"name": "Loot circle 2"}, {"name": "Loot circle 7"}, {"name": "Loot circle 9a"},
                   {"name": "loot circle 40"}, {"name": "Encounter 3"}, {"name": "My zone"}]
        self.assertEqual(automatic_name(ObjectKind.LOOT_CIRCLE, objects), "Loot circle 8")
        self.assertEqual(automatic_name(ObjectKind.LOOT_POLYGON, objects), "Loot polygon 1")
        self.assertEqual(automatic_name(ObjectKind.ENCOUNTER, objects), "Encounter 4")

    def test_new_object_defaults(self) -> None:
        """New zones are visible, unlocked, Rich and Inherit; new encounters start with disabled linked loot."""
        zone = new_object_fields(ObjectKind.LOOT_POLYGON, "abc", "Loot polygon 1")
        self.assertEqual(zone, {"id": "abc", "name": "Loot polygon 1", "kind": "loot_polygon", "visible": True,
                                "locked": False, "tier": None, "abundance": "rich"})
        fresh = new_object_fields(ObjectKind.ENCOUNTER, "e", "Encounter 1")
        self.assertEqual((fresh["excluded"], fresh["linked_loot"]),
                         (False, {"enabled": False, "tier": None, "abundance": "rich", "radius": None}))


    def test_new_object_enters_at_the_top_with_defaults(self) -> None:
        """A new zone is visible, unlocked, Rich, Inherit, named by the next number and placed at index 0."""
        plan = parse_plan(stored_plan([circle("c1")]), PlanForm.STORED)
        updated = with_new_object(plan, ObjectKind.LOOT_CIRCLE, centre={"x": 1.0, "z": 2.0}, radius=10)
        top = updated["objects"][0]
        self.assertEqual((top["name"], top["visible"], top["locked"], top["tier"], top["abundance"]),
                         ("Loot circle 2", True, False, None, "rich"))
        self.assertRegex(top["id"], "^[0-9a-f]{16}$")
        self.assertEqual([item["id"] for item in updated["objects"][1:]], ["c1"])
        self.assertEqual(len(plan["objects"]), 1)
        # A new object outside the bounds is refused
        with self.assertRaises(PlanValidationError):
            with_new_object(plan, ObjectKind.LOOT_CIRCLE, centre={"x": -1.0, "z": 2.0}, radius=10)

    def test_new_object_keeps_the_plan_limits_and_unique_identifiers(self) -> None:
        """A 2,001st object and an identifier that differs only in case are refused."""
        full = parse_plan(stored_plan([circle(f"c{index}") for index in range(2000)]), PlanForm.STORED)
        with self.assertRaises(PlanValidationError):
            with_new_object(full, ObjectKind.LOOT_CIRCLE, centre={"x": 1.0, "z": 2.0}, radius=10)
        plan = parse_plan(stored_plan([circle("c1")]), PlanForm.STORED)
        with self.assertRaises(PlanValidationError) as caught:
            with_new_object(plan, ObjectKind.LOOT_CIRCLE, id="C1", centre={"x": 1.0, "z": 2.0}, radius=10)
        self.assertIn("already used", str(caught.exception))

    def test_new_identifier_avoids_used_identifiers(self) -> None:
        """A random identifier that matches a used one without regard to case is drawn again."""
        with mock.patch.object(mission_map_plan.secrets, "token_hex", side_effect=["abcdef", "123456"]):
            self.assertEqual(new_identifier(["ABCDEF"]), "123456")


if __name__ == "__main__":
    unittest.main()
