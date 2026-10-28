"""Task 6.1: `tweaks set` and `tweaks medical set` (10.4; criteria 5, 6, 8, 27).

Real compositions over the edit fixture; each command run is its own session. The typing of
`tweaks set` follows the JSON type of the current value (10.6).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_cli_fixtures import PROFILE, ChangingTerminal, EditRoot, FakeTerminal  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402

from dayz_serverman.application.medical_features import NO_ZONE_CHANGE  # noqa: E402

ECONOMY = ["tweaks", "set", *PROFILE, "--target", "economy"]
SUPPLY = ["tweaks", "set", *PROFILE, "--target", "loot-supply"]
ZONES = ["tweaks", "medical", "set", *PROFILE, "medical_loot_zones"]
STARTER = ["tweaks", "set", *PROFILE, "--target", "starter-loadout"]


class TweaksSetTests(EditRoot):
    """`tweaks set` with the preview as the review."""

    def test_set_with_yes_in_text_and_json(self) -> None:
        """The review row has the label, the key, the value now and the new one; the file gets the typed value."""
        code, stdout, stderr = self.cli(*ECONOMY, "--set", "ZombieMaxCount=400", "--yes")
        self.assertEqual(code, 0, stderr)
        for text in ("1 validated change(s).", "ZombieMaxCount", "500", "400"):
            self.assertIn(text, stdout)
        self.assertIn("Tweaks applied.", stderr)
        globals_xml = (self.mission / "db" / "globals.xml").read_text(encoding="utf-8")
        self.assertIn('name="ZombieMaxCount" type="0" value="400"', globals_xml)
        code, stdout, stderr = self.cli(*ECONOMY, "--set", "ZombieMaxCount=450", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual(code, 0, stderr)
        self.assertEqual((value["review"]["changed_fields"], value["operations"][0]["kind"]),
                         (["ZombieMaxCount"], "APPLY_MISSION_CONFIGURATION"))

    def test_decline_typing_errors_and_unknown_key_change_nothing(self) -> None:
        """Decline and --json without --yes exit 4; a value of the wrong type and an unknown key exit 2."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("n\n")
        code, stdout, _stderr = self.cli(*ECONOMY, "--set", "ZombieMaxCount=400", stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertIn("ZombieMaxCount", stdout)
        code, stdout, _stderr = self.cli(*ECONOMY, "--set", "ZombieMaxCount=400", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["changed_fields"]),
                         (4, "CONFIRMATION_REQUIRED", ["ZombieMaxCount"]))
        for arguments, text in ((["--set", "ZombieMaxCount=lots"], "The value lots of ZombieMaxCount must be a whole "
                                 "number."),
                                (["--set", "ZombieMaxCont=1"], "Unknown key ZombieMaxCont. Run tweaks show --target "
                                 "economy for the keys.")):
            with self.subTest(arguments=arguments):
                code, _stdout, stderr = self.cli(*ECONOMY, *arguments, "--yes")
                self.assertEqual(code, 2, stderr)
                self.assertIn(text, stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_a_target_whose_file_is_missing_exits_1(self) -> None:
        """Criterion 27: a weather file that the mission does not have is data behind valid names (exit 1)."""
        code, stdout, _stderr = self.cli("tweaks", "set", *PROFILE, "--target", "weather", "--set",
                                         "rain_time_min=5", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (1, "NOT_FOUND"))


class LootSupplySetTests(EditRoot):
    """`tweaks set --target loot-supply` with the preview as the review."""

    def setUp(self) -> None:
        """Add the types file to the fixture mission."""
        super().setUp()
        self.add_medical_files()

    def test_loot_supply_scales_and_returns_to_the_original(self) -> None:
        """A supply percentage scales its group from the originals; 100 puts back the original bytes."""
        types = self.mission / "db" / "types.xml"
        original = types.read_bytes()
        code, stdout, stderr = self.cli(*SUPPLY, "--set", "supply_medical=200", "--set", "ammo_fill=150", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Medical supplies", stdout)
        text = types.read_text(encoding="utf-8")
        for value in ("<nominal>80</nominal>", "<min>60</min>", "<quantmin>15</quantmin>", "<quantmax>60</quantmax>",
                      "<nominal>20</nominal>", "DayZ-ServerMan loot supply"):
            self.assertIn(value, text)
        code, stdout, stderr = self.cli(*SUPPLY, "--set", "supply_medical=100", "--set", "ammo_fill=100", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["operations"][0]["kind"]), (0, "APPLY_MISSION_CONFIGURATION"), stderr)
        self.assertEqual(types.read_bytes(), original)

    def test_loot_supply_decline_and_a_percentage_off_the_steps(self) -> None:
        """Decline exits 4; a percentage that is not a step of 5 is refused by the preview (exit 2)."""
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("n\n")
        code, _stdout, stderr = self.cli(*SUPPLY, "--set", "supply_ammo=150", stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1), stderr)
        code, stdout, _stderr = self.cli(*SUPPLY, "--set", "supply_ammo=152", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "INVALID_REQUEST"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)


class MedicalSetTests(EditRoot):
    """`tweaks medical set FEATURE on/off` with the preview as the review."""

    def setUp(self) -> None:
        """Add the medical files to the fixture mission."""
        super().setUp()
        self.add_medical_files()

    def test_zone_toggle_without_a_changing_group_exits_0_and_says_so(self) -> None:
        """On and off succeed without a medical group; the end line says once that no file was changed."""
        proto = self.mission / "mapgroupproto.xml"
        before = saved_state(self.manager, self.dayz)
        for state in ("on", "off"):
            with self.subTest(state=state):
                code, stdout, stderr = self.cli(*ZONES, state, "--yes")
                self.assertEqual(code, 0, stderr)
                self.assertEqual((stdout + stderr).count(NO_ZONE_CHANGE), 1, stdout + stderr)
                self.assertIn(NO_ZONE_CHANGE, stderr)
                self.assertNotIn("Medical loot setting applied.", stderr)
        code, stdout, stderr = self.cli(*ZONES, "on", "--yes", "--json")
        result = json.loads(stdout)["value"]["operations"][0]["result"]
        self.assertEqual((code, result["changed_fields"], result["message"]), (0, [], NO_ZONE_CHANGE), stderr)
        self.assertEqual(proto.read_text(encoding="utf-8"), "<groups/>")
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_zone_layout_refusal_names_the_cause_and_keeps_its_exit_code(self) -> None:
        """A group with another element between its usages refuses as recovery-required, naming the group."""
        proto = self.mission / "mapgroupproto.xml"
        proto.write_text('<prototype><group name="Hospital_A"><usage name="Medic"/><value name="Tier1"/>'
                         '<usage name="Town"/></group></prototype>', encoding="utf-8")
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*ZONES, "on", "--yes")
        self.assertEqual(code, 6, stderr)
        self.assertIn("mapgroupproto.xml has a layout that this setting cannot change safely: group Hospital_A has "
                      "another element between usage and category. No file was changed.", stderr)
        code, stdout, _stderr = self.cli(*ZONES, "on", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (6, "RECOVERY_REQUIRED"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_a_medical_group_missing_from_the_original_refuses_like_differs(self) -> None:
        """A qualified group that the stored original lacks keeps the exit code of "differs from both" and names it."""
        proto = self.mission / "mapgroupproto.xml"
        proto.write_text('<prototype><group name="Hospital_A"><usage name="Medic"/><usage name="Town"/></group>'
                         '<group name="Clinic_New"><usage name="Medic"/></group></prototype>', encoding="utf-8")
        (self.mission / ".dayz_manager_backups").mkdir()
        (self.mission / ".dayz_manager_backups" / "mapgroupproto.xml").write_text(
            '<prototype><group name="Hospital_A"><usage name="Medic"/><usage name="Town"/></group></prototype>',
            encoding="utf-8")
        before = saved_state(self.manager, self.dayz)
        code, _stdout, stderr = self.cli(*ZONES, "on", "--yes")
        self.assertEqual(code, 3, stderr)
        self.assertIn("mapgroupproto.xml differs from its stored original: group Clinic_New is not in the stored "
                      "original. No file was changed.", stderr)
        code, stdout, _stderr = self.cli(*ZONES, "on", "--yes", "--json")
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (3, "REVISION_CONFLICT"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)


class StarterItemsTests(EditRoot):
    """`tweaks set --target starter-loadout`: own items and the mission's own items, without touching its code."""

    def test_mission_items_off_and_own_items(self) -> None:
        """Turning the mission's items off keeps its code; on again restores the file."""
        self.add_legacy_starter()
        init = self.mission / "init.c"
        before = init.read_text(encoding="utf-8")
        code, stdout, stderr = self.cli(*STARTER, "--set", "mission_items=off", "--yes", "--json")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["value"]["operations"][0]["kind"], "APPLY_MISSION_CONFIGURATION")
        changed = init.read_text(encoding="utf-8")
        self.assertIn("if ( false )", changed)
        self.assertIn('CreateInInventory( "WaterBottle" )', changed)
        self.assertEqual(self.cli(*STARTER, "--set", "mission_items=on", "--yes")[0], 0)
        self.assertEqual(init.read_text(encoding="utf-8"), before)


class StaleReviewTests(EditRoot):
    """QF-63: the file changes while the question waits; the reviewed apply refuses with exit 3, the edit kept."""

    def stale(self, arguments: list[str], path: Path, edit) -> None:
        """Answer "y" after another editor changed `path`; exit 3 and the other editor's bytes stay."""
        changed: list[bytes] = []

        def change() -> None:
            """Edit the file as another editor would, and remember its bytes."""
            path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
            changed.append(path.read_bytes())

        code, stdout, stderr = self.cli(*arguments, stdin=ChangingTerminal(change))
        self.assertEqual(code, 3, stderr)
        self.assertTrue(stdout)
        self.assertEqual(path.read_bytes(), changed[0])

    def test_tweaks_set(self) -> None:
        """`tweaks set` keeps the other editor's value instead of the reviewed one."""
        globals_xml = self.mission / "db" / "globals.xml"
        self.stale([*ECONOMY, "--set", "ZombieMaxCount=400"], globals_xml,
                   lambda text: text.replace('name="ZombieMaxCount" type="0" value="500"',
                                             'name="ZombieMaxCount" type="0" value="321"'))
        self.assertIn('value="321"', globals_xml.read_text(encoding="utf-8"))

    def test_tweaks_set_loot_supply(self) -> None:
        """`tweaks set --target loot-supply` keeps the other editor's types file."""
        self.add_medical_files()
        self.stale([*SUPPLY, "--set", "supply_medical=200"], self.mission / "db" / "types.xml",
                   lambda text: text.replace("<nominal>40</nominal>", "<nominal>41</nominal>"))

    def test_tweaks_set_starter_items(self) -> None:
        """`tweaks set --target starter-loadout` keeps the other editor's init.c and adds no layer."""
        self.add_legacy_starter()
        init = self.mission / "init.c"
        self.stale([*STARTER, "--set", "mission_items=off"], init, lambda text: text + "// edited elsewhere\n")
        self.assertNotIn("DayZ-ServerMan", init.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
