"""Verify the Draft flag after a window JSON round trip and the canonical fingerprint numbers (5.2, T2.3-F4)."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from dayz_serverman.domain.mission_map_plan import PlanForm, configuration_fingerprint, parse_plan
from dayz_serverman.repositories.mission_map_records import AssociationRecordStore
from tests.test_mission_map_editor_load_save import CoordinatorFixture
from tests.test_mission_map_plan import circle, encounter, stored_plan
from tests.test_mission_map_records import VANILLA, applied, sha


def window_number(number: float | int) -> str:
    """Return the JSON text of a number as the window writes it: binary64, -0 as 0, integral without a point."""
    value = float(number)
    if value == 0:
        return "0"
    # JavaScript Number to string: integral values below 1e21 have no fraction and no exponent
    return str(int(value)) if value.is_integer() and abs(value) < 1e21 else repr(value)


def window_json(value: Any) -> str:
    """Return the JSON text that JSON.stringify of the parsed value gives in the window."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return window_number(value)
    if isinstance(value, list):
        return "[" + ",".join(window_json(item) for item in value) + "]"
    return "{" + ",".join(f"{json.dumps(key)}:{window_json(item)}" for key, item in value.items()) + "}"


def window_round_trip(value: Any) -> Any:
    """Return the value after the window parsed it and sent it back as JSON."""
    return json.loads(window_json(value))


def wolf(properties: dict[str, Any]) -> dict[str, Any]:
    """Return a wolf encounter with open provider properties."""
    return dict(encounter("w1", "native_wolf"), properties=properties)


# Open properties with every number form that the window does not keep
PROPERTIES = {"group_size": 2.0, "offset": -0.0, "large": 2 ** 53 + 1, "nested": {"list": [1.0, -0.0, 0.1, 1e-7]}}


class DraftFlagRoundTripTests(CoordinatorFixture):
    """The Draft flag comes from the backend fingerprint and survives the window's number forms."""

    def applied_as(self, plan: dict[str, Any]) -> None:
        """Store a last-applied record whose fingerprint is the configuration of the plan."""
        key = plan["association"]["mission_key"]
        record = applied(key=key)
        record["configuration_fingerprint"] = configuration_fingerprint(parse_plan(plan, PlanForm.STORED))
        AssociationRecordStore(self.area, "main", key, "applied").save(record, {sha(VANILLA): VANILLA}, None)

    def test_window_round_trip_keeps_the_draft_flag_false(self) -> None:
        """An applied plan loaded, round-tripped by the window and saved again shows no Draft changes."""
        plan = self.draft(objects=[circle("c1", x=100.0, z=0.0), wolf(copy.deepcopy(PROPERTIES))])
        self.save(plan)
        self.applied_as(plan)
        loaded = self.load()
        self.assertFalse(loaded["draft_changes"])
        sent = window_round_trip(loaded["plan"])
        # The window changed the number forms, as JSON.stringify does
        self.assertEqual(sent["objects"][1]["properties"]["group_size"], 2)
        self.assertIsInstance(sent["objects"][1]["properties"]["group_size"], int)
        self.assertNotEqual(sent["objects"][1]["properties"]["large"], PROPERTIES["large"])
        result = self.save(sent, 0)
        self.assertFalse(result["draft_changes"])
        self.assertEqual(result["fingerprint"], configuration_fingerprint(parse_plan(plan, PlanForm.STORED)))
        self.assertFalse(self.load()["draft_changes"])
        # A real configuration change still shows, and undoing it in the window clears it again
        changed = copy.deepcopy(sent)
        changed["objects"][1]["properties"]["group_size"] = 2.5
        self.assertTrue(self.save(changed, 1)["draft_changes"])
        self.assertTrue(self.load()["draft_changes"])
        self.assertFalse(self.save(sent, 2)["draft_changes"])

    def test_presentation_edits_keep_the_draft_flag_false(self) -> None:
        """A rename, hide or lock after a round trip changes no mission file, so Draft changes stays false."""
        plan = self.draft(objects=[circle("c1", x=100.0, z=0.0)])
        self.save(plan)
        self.applied_as(plan)
        sent = window_round_trip(self.load()["plan"])
        sent["objects"][0].update(name="Renamed", visible=False, locked=True)
        self.assertFalse(self.save(sent, 0)["draft_changes"])


class CanonicalNumberTests(unittest.TestCase):
    """T2.3-F4: each number has one canonical form in the fingerprint; stored data keeps its own form."""

    def fingerprint(self, properties: dict[str, Any], x: float | int = 100.0) -> str:
        """Return the fingerprint of a plan with one circle and one wolf encounter."""
        return configuration_fingerprint(parse_plan(stored_plan([circle("c1", x=x), wolf(properties)]), PlanForm.STORED))

    def test_negative_zero_and_integral_numbers_are_one_number(self) -> None:
        """-0.0 equals 0.0 and 0, and 2.0 equals 2, in geometry and at any depth of open properties."""
        base = self.fingerprint({"a": 0, "b": [2, {"c": 0}]}, x=0)
        self.assertEqual(self.fingerprint({"a": -0.0, "b": [2.0, {"c": -0.0}]}, x=-0.0), base)
        self.assertEqual(self.fingerprint({"a": 0.0, "b": [2.0, {"c": 0.0}]}, x=0.0), base)

    def test_numbers_take_their_binary64_value(self) -> None:
        """An integer beyond 2**53 equals the binary64 value that the window keeps; distinct values stay distinct."""
        self.assertEqual(self.fingerprint({"n": 2 ** 53 + 1}), self.fingerprint({"n": 2 ** 53}))
        self.assertEqual(self.fingerprint({"n": 2 ** 53 + 1}), self.fingerprint({"n": float(2 ** 53)}))
        self.assertNotEqual(self.fingerprint({"n": 2 ** 53 + 2}), self.fingerprint({"n": 2 ** 53}))
        self.assertNotEqual(self.fingerprint({"n": 2.5}), self.fingerprint({"n": 2}))
        self.assertEqual(self.fingerprint({"n": 0.1}), self.fingerprint({"n": json.loads("0.10000000000000000555")}))
        self.assertEqual(self.fingerprint({"n": 1e-7}), self.fingerprint({"n": json.loads("1E-7")}))

    def test_booleans_and_text_keep_their_type(self) -> None:
        """True is not 1, False is not 0, and "2" is not 2."""
        self.assertNotEqual(self.fingerprint({"n": True}), self.fingerprint({"n": 1}))
        self.assertNotEqual(self.fingerprint({"n": False}), self.fingerprint({"n": 0}))
        self.assertNotEqual(self.fingerprint({"n": "2"}), self.fingerprint({"n": 2}))

    def test_fingerprint_leaves_the_plan_unchanged(self) -> None:
        """Only the fingerprint content is canonical; the parsed and stored plan keep their numbers."""
        plan = parse_plan(stored_plan([circle("c1", x=-0.0), wolf({"n": 2.0, "big": 2 ** 53 + 1})]), PlanForm.STORED)
        before = json.dumps(plan, sort_keys=True)
        configuration_fingerprint(plan)
        self.assertEqual(json.dumps(plan, sort_keys=True), before)
        self.assertEqual(json.dumps(plan["objects"][1]["properties"]), '{"n": 2.0, "big": 9007199254740993}')


if __name__ == "__main__":
    unittest.main()
