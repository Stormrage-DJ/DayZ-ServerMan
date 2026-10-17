"""Leaf 10.33 follow-up: the destination availability of import and copy, and the revision check first.

Architect 2026-10-17 03:38:11 in evidence 10.33: the coordinator builds the check for each call from the
capability and provider read. Encounters share the rule of Apply; a tier that the value layer cannot hold is
`tier_not_holdable`; the 6.11 family gate is not a source. A stale revision refuses before the confirmation.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import ChangingTerminal  # noqa: E402
from map_cli_fixtures import PROFILE_ID, SHOW, MapFixture, keep_fixture_family_unverified  # noqa: E402
from test_mission_map_plan import circle, encounter, stored_plan  # noqa: E402
from tests.test_mission_map_transfer import TransferFixture  # noqa: E402

from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.map_publish_wording import BANDIT_REASON, WOLF_BEAR_REASON  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.mission_map_merge import destination_availability  # noqa: E402
from dayz_serverman.domain.mission_map_preview_changes import unavailable_encounters  # noqa: E402
from dayz_serverman.domain.models import RevisionConflict  # noqa: E402

PROVIDERS = {
    "native_infected": {"available": True, "reason": None, "event_names": ["InfectedCity"]},
    "ai_bandits": {"available": False, "reason": BANDIT_REASON, "event_names": []},
    "native_wolf": {"available": False, "reason": WOLF_BEAR_REASON, "event_names": []},
    "native_bear": {"available": False, "reason": WOLF_BEAR_REASON, "event_names": []},
}
HOLDLESS = [{"operation": "tier:Tier4", "reason": "tier_not_holdable"}, {"operation": "tier_layer", "reason": "x"}]


def with_tier(item: dict, tier: str | None) -> dict:
    """Return a loot object with another tier."""
    return {**item, "tier": tier}


def infected(identifier: str, event: str) -> dict:
    """Return a native infected encounter of one event."""
    item = encounter(identifier)
    return {**item, "properties": {**item["properties"], "event_name": event}}


class DestinationAvailabilityTests(unittest.TestCase):
    """The per-object check, without a mission read."""

    def test_each_reason_and_the_available_objects(self) -> None:
        """Providers, events not offered, wolves and bears, and tiers; available objects give None."""
        check = destination_availability(HOLDLESS, PROVIDERS)
        loot = encounter("e6")
        linked = {**loot, "linked_loot": {**loot["linked_loot"], "enabled": True, "tier": "Tier4", "radius": 30.0}}
        cases = [(infected("e1", "InfectedCity"), None), (infected("e2", "OtherEvent"), "event_not_offered"),
                 (encounter("e3", "ai_bandits"), BANDIT_REASON), (encounter("e4", "native_wolf"), WOLF_BEAR_REASON),
                 (encounter("e5", "native_bear"), WOLF_BEAR_REASON), (linked, "tier_not_holdable"),
                 (with_tier(circle("c1"), "Tier4"), "tier_not_holdable"), (with_tier(circle("c2"), "Tier2"), None),
                 (with_tier(circle("c3"), None), None)]
        for item, reason in cases:
            with self.subTest(object=item["id"]):
                self.assertEqual(check(item), reason)
        unavailable = {**PROVIDERS, "native_infected": {"available": False, "reason": "territory_not_registered"}}
        self.assertEqual(destination_availability([], unavailable)(infected("e7", "InfectedCity")),
                         "territory_not_registered")

    def test_the_encounter_rule_is_the_rule_of_apply(self) -> None:
        """For each encounter, the import reason equals the reason of `unavailable_encounters`."""
        objects = [infected("e1", "InfectedCity"), infected("e2", "OtherEvent"), encounter("e3", "ai_bandits"),
                   encounter("e4", "native_wolf")]
        apply = {entry["id"]: entry["reason"] for entry in unavailable_encounters(objects, PROVIDERS)}
        check = destination_availability([], PROVIDERS)
        self.assertEqual({item["id"]: check(item) for item in objects if check(item) is not None}, apply)


class RevisionFirstTests(TransferFixture):
    """`_commit` compares the revision first, before the plan limit and the confirmation."""

    def test_a_stale_revision_refuses_before_the_confirmation_and_the_limit(self) -> None:
        """Another terrain needs a confirmation; with a stale revision the answer is RevisionConflict."""
        other = self.text([circle("x1")], terrain={"terrain_id": "other.terrain", "bounds": {
            "x_min": 0.0, "z_min": 0.0, "width": 1000.0, "height": 1000.0}, "bounds_source": "manual",
            "bounds_confirmed": True})
        self.assertTrue(self.transfer.preview_import("main", other, "merge")["confirmation_required"])
        with self.assertRaises(RevisionConflict):
            self.transfer.import_plan("main", other, "merge", 7, False)
        many = self.text([circle(f"c{index}") for index in range(2000)])
        with self.assertRaises(RevisionConflict):
            self.transfer.import_plan("main", many, "merge", 7, True)


class ImportAvailabilityTests(MapFixture):
    """The import preview through the runner lists unavailable objects; they need no --confirm-listed."""

    def setUp(self) -> None:
        """Keep a folder for the input files beside the two roots."""
        super().setUp()
        self.inputs = self.manager.parent / "inputs"
        self.inputs.mkdir()

    def file(self, objects: list[dict]) -> str:
        """Write one plan file of these objects on the fixture terrain and return its path."""
        path = self.inputs / "plan.json"
        path.write_text(json.dumps(stored_plan(objects)), encoding="utf-8")
        return str(path)

    def test_a_wolf_encounter_is_listed_and_imported(self) -> None:
        """The 7.9 text of map show; no --confirm-listed; an unverified family adds no entry for the circle."""
        keep_fixture_family_unverified(self)
        path = self.file([circle("inside"), encounter("wolves", "native_wolf")])
        code, stdout, stderr = self.cli("map", "import", "--profile", PROFILE_ID, "--from-file", path, "--mode",
                                        "replace", "--yes")
        lines = stdout.splitlines()
        self.assertEqual(code, 0, stderr)
        start = lines.index(words.UNAVAILABLE_HEADING)
        self.assertEqual(lines[start + 1:start + 2], [f"  Encounter 1: {WOLF_BEAR_REASON}"])
        self.assertFalse(any(line.startswith("  Loot circle 1:") for line in lines))
        code, stdout, _stderr = self.cli(*SHOW, "--json")
        self.assertEqual([item["id"] for item in json.loads(stdout)["value"]["plan"]["objects"]], ["inside", "wolves"])

    def test_a_change_during_the_question_is_stale_before_the_confirmation(self) -> None:
        """New bounds make the report need a confirmation; the import exits 3 with the stale text instead."""
        path = self.file([circle("inside")])

        def change_bounds() -> None:
            """Save a plan with other bounds while the question waits."""
            composition = build_composition(self.manager)
            try:
                plan = stored_plan([circle("changed")])
                plan["terrain"]["bounds"]["width"] = 2000.0
                plan["association"] = composition.mission_map.plans.active_association(PROFILE_ID)
                plan["background"] = None
                composition.mission_map.plans.save(PROFILE_ID, plan, 0)
            finally:
                composition.shutdown.request_shutdown()
                composition.shutdown.wait_for_close(5)

        code, _stdout, stderr = self.cli("map", "import", "--profile", PROFILE_ID, "--from-file", path, "--mode",
                                         "merge", stdin=ChangingTerminal(change_bounds))
        self.assertEqual(code, 3, stderr)
        self.assertIn(words.STALE_PLAN, stderr)
        code, stdout, _stderr = self.cli(*SHOW, "--json")
        self.assertEqual([item["id"] for item in json.loads(stdout)["value"]["plan"]["objects"]], ["changed"])


if __name__ == "__main__":
    unittest.main()
