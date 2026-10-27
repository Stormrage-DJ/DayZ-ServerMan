"""Verify linked loot and covering encounters in evaluation, Inspect and capacity targets (7.4)."""

from __future__ import annotations

import math
import unittest

from dayz_serverman.domain.mission_map_capacity_targets import placement_adjustments
from dayz_serverman.domain.mission_map_evaluation import covering_encounters, evaluate_location, evaluate_plan
from dayz_serverman.domain.mission_map_values import ABUNDANCE_PERCENT, GLOBAL_ABUNDANCE_STEPS
from tests.mission_map_evaluation_support import finish, fixture
from tests.mission_map_inspection_support import InspectionFixture
from tests.test_mission_map_plan import circle, encounter


def linked(identifier: str = "e1", x: float = 500.0, z: float = 500.0, radius: float = 300.0, *,
           tier: str | None = None, abundance: str = "rich", **fields) -> dict:
    """Return an infected encounter with enabled linked loot on its own centre and spawn radius."""
    item = encounter(identifier)
    item.update(centre={"x": x, "z": z}, radius=radius, name=f"Encounter {identifier}", **fields)
    item["linked_loot"].update(enabled=True, tier=tier, abundance=abundance)
    return item


class LinkedLootEvaluationTests(unittest.TestCase):
    """Linked loot is a loot area at its encounter's list position; the encounter's state does not remove it."""

    def test_linked_loot_wins_by_list_position_and_follows_the_encounter_geometry(self) -> None:
        """Above a zone the linked loot wins; below it the zone wins; a move or resize moves the loot area."""
        zone = {**circle("z1", 500, 500, 400), "abundance": "sparse"}
        plan, capabilities, values = fixture([linked(abundance="very_rich", tier="Tier2"), zone])
        answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
        self.assertEqual((answer["winner"]["id"], answer["winner"]["kind"]), ("e1", "encounter"))
        self.assertEqual((answer["effective_tier"], answer["abundance"]["zone_preset"]), (["Tier2"], "very_rich"))
        plan["objects"].reverse()
        answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
        self.assertEqual((answer["winner"]["id"], answer["tier_object"]["id"]), ("z1", "e1"))
        self.assertEqual(answer["abundance"]["zone"], ABUNDANCE_PERCENT["sparse"])
        plan["objects"].reverse()
        # The loot area has no geometry of its own: it is the encounter centre and spawn radius (D6)
        plan["objects"][0].update(centre={"x": 100.0, "z": 100.0}, radius=50.0)
        self.assertEqual(evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)["winner"]["id"], "z1")
        self.assertEqual(evaluate_location(plan, capabilities, values, 150, 100, sample_cell=False)["winner"]["id"], "e1")
        self.assertIsNone(evaluate_location(plan, capabilities, values, 151, 100, sample_cell=False)["winner"])

    def test_inherit_linked_loot_changes_capacity_only_and_r08_rows_add_once(self) -> None:
        """Rich and Inherit: the original tier stays; every preset and global step adds once."""
        plan, capabilities, values = fixture([linked()])
        answer = evaluate_location(plan, capabilities, values, 500, 500)
        self.assertTrue(answer["abundance_only"])
        self.assertEqual((answer["tier_source"], answer["effective_tier"]), ("original", answer["original_tier"]))
        for global_value in GLOBAL_ABUNDANCE_STEPS:
            for preset, zone in ABUNDANCE_PERCENT.items():
                with self.subTest(global_value=global_value, preset=preset):
                    plan["global_abundance"] = global_value
                    plan["objects"][0]["linked_loot"]["abundance"] = preset
                    result = evaluate_location(plan, capabilities, values, 500, 500)
                    self.assertEqual((result["abundance"]["zone"], result["abundance"]["effective"]),
                                     (zone, global_value + zone))
                    entry = evaluate_plan(plan, capabilities)["objects"][0]
                    self.assertEqual((entry["zone_adjustment"], entry["effective_adjustment"]), (zone, global_value + zone))

    def test_disabled_linked_loot_contributes_nothing_and_exclusion_keeps_enabled_loot(self) -> None:
        """Disabled loot is no area; an excluded, hidden or locked encounter keeps its enabled loot (R11, R12)."""
        disabled = encounter("e1")
        disabled["linked_loot"].update(tier="Tier3", abundance="very_rich")
        plan, capabilities, values = fixture([disabled])
        answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
        self.assertIsNone(answer["winner"])
        self.assertEqual(answer["abundance"]["effective"], 0)
        self.assertEqual(evaluate_plan(plan, capabilities)["objects"][0]["zone_adjustment"], 0)
        kept = linked(tier="Tier3", excluded=True, visible=False, locked=True)
        plan, capabilities, values = fixture([kept])
        answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
        self.assertEqual((answer["winner"]["id"], answer["effective_tier"]), ("e1", ["Tier3"]))
        self.assertEqual([item["id"] for item in answer["hidden_contributors"]], ["e1"])
        self.assertEqual(evaluate_plan(plan, capabilities)["objects"][0]["zone_adjustment"], 50)

    def test_object_tier_sets_of_linked_loot_use_the_spawn_circle(self) -> None:
        """An object read of an encounter samples its linked loot circle; a disabled one still has the circle."""
        plan, capabilities, values = fixture([linked(tier="Tier4", radius=200.0)])
        result = finish(plan, capabilities, values, "e1")["result"]
        self.assertGreater(result["cell_count"], 0)
        self.assertEqual(result["effective_tiers"], ["Tier4"])
        plan["objects"][0]["linked_loot"]["enabled"] = False
        result = finish(plan, capabilities, values, "e1")["result"]
        self.assertGreater(result["cell_count"], 0)
        self.assertEqual(result["effective_tiers"], result["original_tiers"])

    def test_linked_loot_presets_adjust_capacity_targets(self) -> None:
        """Placements under linked loot take its preset plus the global value (R07, R08); outside, only the global."""
        plan, capabilities, _values = fixture([linked(abundance="very_rich", radius=100.0)])
        plan["global_abundance"] = -25
        self.assertEqual(placement_adjustments(plan, [(500, 500), (600, 500), (601, 500)]), (75, 75, -25))
        plan["objects"][0]["linked_loot"]["enabled"] = False
        self.assertEqual(placement_adjustments(plan, [(500, 500)]), (-25,))


class CoveringEncounterTests(unittest.TestCase):
    """Inspect lists every encounter whose closed spawn circle covers the evaluated point, in plan order."""

    def test_closed_circle_plan_order_and_every_state(self) -> None:
        """Edges count; hidden, excluded and disabled-loot encounters are listed; a radius-less one is not."""
        bandit = encounter("b1", "ai_bandits")
        bandit["linked_loot"].update(enabled=True, radius=300.0)
        objects = [linked("e1", radius=100.0, visible=False), encounter("e2"), bandit,
                   linked("e3", radius=100.0, excluded=True), circle("z1", 500, 500, 400)]
        objects[1].update(centre={"x": 540.0, "z": 500.0}, radius=40.0)
        plan, capabilities, values = fixture(objects)
        self.assertEqual([item["id"] for item in covering_encounters(plan, 500.0, 500.0)], ["e1", "e2", "e3"])
        self.assertEqual(covering_encounters(plan, 500.0, 500.0)[0],
                         {"id": "e1", "name": "Encounter e1", "provider": "native_infected", "hidden": True,
                          "excluded": False})
        self.assertTrue(covering_encounters(plan, 500.0, 500.0)[2]["excluded"])
        self.assertEqual([item["id"] for item in covering_encounters(plan, 600.0, 500.0)], ["e1", "e3"])
        self.assertEqual(covering_encounters(plan, math.nextafter(600.0, math.inf), 500.0), [])
        answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
        self.assertEqual([item["id"] for item in answer["encounters"]], ["e1", "e2", "e3"])
        self.assertEqual(answer["winner"]["id"], "e1")

    def test_wolf_and_bear_encounters_cover_and_link_loot(self) -> None:
        """Wolves and bears have a spawn radius: both are listed as covering, and their enabled linked loot can win."""
        wolf, bear = encounter("w1", "native_wolf"), encounter("b1", "native_bear")
        bear["linked_loot"].update(enabled=True, abundance="very_rich")
        plan, capabilities, values = fixture([wolf, bear])
        answer = evaluate_location(plan, capabilities, values, 500, 500, sample_cell=False)
        self.assertEqual([(item["id"], item["provider"]) for item in answer["encounters"]],
                         [("w1", "native_wolf"), ("b1", "native_bear")])
        self.assertEqual((answer["winner"]["id"], answer["abundance"]["zone_preset"]), ("b1", "very_rich"))

    def test_covering_uses_the_same_point_as_the_winning_zone(self) -> None:
        """With tier writes the D9 cell centre is tested; without them the clicked point is tested."""
        plan, capabilities, values = fixture([linked("e1", 62.5, 83.33333333333333, 1.0)])
        answer = evaluate_location(plan, capabilities, values, 0, 0)
        self.assertEqual(answer["cell"]["i"], 0)
        self.assertEqual(([item["id"] for item in answer["encounters"]], answer["winner"]["id"]), (["e1"], "e1"))
        plan["terrain"]["bounds_confirmed"] = False
        answer = evaluate_location(plan, capabilities, values, 0, 0)
        self.assertEqual((answer["encounters"], answer["winner"]), ([], None))


class InspectCoveringEncounterTests(InspectionFixture):
    """A point answer of the Inspect service carries the covering encounters and the linked loot winner."""

    def test_point_answer_lists_covering_encounters(self) -> None:
        """The saved plan's encounter covers the point; its linked loot is the winning zone."""
        self.saved([linked("e1", radius=200.0, abundance="normal"), circle("c1", x=500.0, z=500.0, radius=600.0)])
        answer, _phases = self.finish(self.start({"point": {"x": 500.0, "z": 500.0}}))
        result = answer["result"]
        self.assertEqual(answer["status"], "done")
        self.assertEqual([item["id"] for item in result["encounters"]], ["e1"])
        self.assertEqual((result["winner"]["id"], result["abundance"]["zone_preset"]), ("e1", "normal"))
        answer, _phases = self.finish(self.start({"point": {"x": 900.0, "z": 900.0}}))
        self.assertEqual((answer["result"]["encounters"], answer["result"]["winner"]["id"]), ([], "c1"))


if __name__ == "__main__":
    unittest.main()
