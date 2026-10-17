"""Leaf 10.4: `map show` and the specs of both map reads through the runner on real compositions."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from lifecycle_cli_fixtures import saved_state  # noqa: E402
from map_cli_fixtures import INSPECT, PROFILE_ID, SHOW, MapFixture, keep_fixture_family_unverified  # noqa: E402
from mission_map_inspection_support import write_mission  # noqa: E402
from test_mission_map_plan import circle, encounter, square  # noqa: E402

from dayz_serverman.cli import help_wording  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.parser import command_list, leaf_help  # noqa: E402
from dayz_serverman.cli.registry import Kind, ProfileRule  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.domain.mission_map_loot_report_texts import UNAVAILABLE_TEXTS  # noqa: E402

class MapSpecTests(unittest.TestCase):
    """The specs, the help texts and the parser of the two commands."""

    def test_specs_are_observer_reads_of_phase_10(self) -> None:
        """Both commands are READ commands with the default profile rule, phase 10, and their handlers."""
        table = {command.name: command for command in COMMANDS}
        for name, methods in (("map show", {"load_mission_map"}),
                              ("map inspect", {"inspect_mission_map", "continue_mission_map_inspection",
                                               "cancel_mission_map_inspection"})):
            with self.subTest(command=name):
                command = table[name]
                self.assertEqual((command.kind, command.phase, command.profile, command.pending),
                                 (Kind.READ, 10, ProfileRule.READ_DEFAULT, False))
                self.assertEqual(command.bridge_methods, methods)
        # Later map leaves add their own phase 10 commands (D20)
        self.assertLessEqual({"map show", "map inspect"}, {command.name for command in COMMANDS if command.phase == 10})

    def test_help_texts_are_the_product_owner_texts(self) -> None:
        """The noun, the two commands and the two option destinations use the texts of evidence 10.1."""
        self.assertEqual(help_wording.NOUN_TEXTS["map"],
                         "Show and change the map plan of a mission, apply it, and restore the mission files.")
        self.assertEqual(help_wording.COMMAND_TEXTS["map show"],
                         "Show the saved map plan of a profile and the state of its mission.")
        self.assertEqual(help_wording.COMMAND_TEXTS["map inspect"],
                         "Show the loot tier and the building capacity at one point, or for one object.")
        self.assertEqual(help_wording.OPTION_TEXTS["point"],
                         "A point as X,Z in native metres, for example 7500,7500. For a negative number, write --point=-5,3. Use it or --object.")
        self.assertEqual(help_wording.OPTION_TEXTS["object_id"],
                         "A plan object, by its ID as map show lists it. Use it or --point.")
        self.assertIn("map show", command_list())
        page = leaf_help(next(command for command in COMMANDS if command.name == "map inspect"))
        self.assertIn("--point X,Z", page)
        self.assertIn("--object ID", page)
        self.assertNotIn("--yes", page)


class MapShowTests(MapFixture):
    """`map show` on the fixture profile: output, JSON, exit codes and the read-only guarantee."""

    def setUp(self) -> None:
        """Keep the fixture mission enoch unverified, as the 6.11 refusal path needs."""
        super().setUp()
        keep_fixture_family_unverified(self)

    def test_text_names_the_state_the_terrain_and_the_objects(self) -> None:
        """The window facts as lines: identity, plan state, terrain, tier writes, family text, objects."""
        write_mission(self.mission)
        self.save_objects([circle("c1"), square("p1"), encounter("e1")])
        code, stdout, stderr = self.cli(*SHOW)
        self.assertEqual((code, stderr), (0, ""))
        lines = stdout.splitlines()
        self.assertIn("Plan state: Valid", lines)
        self.assertIn("Mission: dayzOffline.enoch", lines)
        self.assertIn("Revision: 1", lines)
        self.assertIn("Tier writes: Available", lines)
        self.assertIn("Global container capacity: 0%", lines)
        self.assertIn("Objects in priority order", lines)
        self.assertIn("Terrain bounds: confirmed. X minimum 0 m, Z minimum 0 m, width 1000 m, height 1000 m.", lines)
        self.assertTrue(any("The terrain family enoch is not one of them." in line for line in lines))
        rows = {line.split()[0]: line for line in lines if line[:2] in ("c1", "p1", "e1")}
        self.assertIn("Loot circle", rows["c1"])
        self.assertIn("Rich +50%", rows["c1"])
        self.assertIn("Tier2", rows["p1"])
        self.assertIn("Sparse -25%", rows["p1"])
        self.assertIn("Not available: Infected areas are not available on this mission:", rows["e1"])
        self.assertIn("The saved plan has changes that no Apply has written.", lines)

    def test_providers_and_capabilities_that_are_not_available(self) -> None:
        """Wolves and bears print the 7.9 text; a mission without a tier map names its reasons."""
        code, stdout, _stderr = self.cli(*SHOW)
        self.assertEqual(code, 0)
        wolf = "Wolf and bear territories are not supported in this version; the pack territory mapping is not verified."
        self.assertIn("  Infected areas: cfgenvironment.xml, env/zombie_territories.xml or db/events.xml is missing.",
                      stdout.splitlines())
        self.assertIn(f"  Wolves: {wolf}", stdout.splitlines())
        self.assertIn(f"  Bears: {wolf}", stdout.splitlines())
        self.assertIn("  Tier writes: " + UNAVAILABLE_TEXTS["areaflags_missing"], stdout.splitlines())
        self.assertIn("  Original tiers: This mission has no areaflags.map, so its original tiers cannot be shown.",
                      stdout.splitlines())
        self.assertIn("Tier writes: Not available", stdout.splitlines())

    def test_json_is_the_load_answer_unchanged(self) -> None:
        """The JSON `value` equals the `load_mission_map` answer, with no profile_id added."""
        code, stdout, stderr = self.cli(*SHOW, "--json")
        self.assertEqual((code, stderr), (0, ""))
        document = json.loads(stdout)
        self.assertEqual((document["command"], document["success"]), ("map show", True))
        composition = build_composition(self.manager)
        try:
            answer = composition.bridge.dispatch({"contract_version": 1, "request_id": "t", "method":
                                                  "load_mission_map", "parameters": {"profile_id": PROFILE_ID}})
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)
        self.assertEqual(document["value"], json.loads(json.dumps(answer["value"])))

    def test_invalid_saved_data_names_the_way_out(self) -> None:
        """A damaged plan file: the window text, then the CLI line that names map set-aside; exit 0."""
        self.plan_file().write_text("{", encoding="utf-8")
        code, stdout, _stderr = self.cli(*SHOW)
        lines = stdout.splitlines()
        self.assertEqual(code, 0)
        self.assertIn("Plan state: Not valid", lines)
        self.assertIn("Read only: the saved map plan is not valid. Set aside invalid plan keeps it under a dated "
                      "name and starts a new, empty plan.", lines)
        self.assertIn("Run map set-aside.", lines)

    def test_a_mission_without_a_saved_plan(self) -> None:
        """No plan file and no tier map: the state says so, the bounds are not confirmed, no object list prints."""
        self.plan_file().unlink()
        code, stdout, _stderr = self.cli(*SHOW)
        lines = stdout.splitlines()
        self.assertEqual(code, 0)
        self.assertIn("Plan state: No saved plan", lines)
        self.assertIn("Terrain bounds: not confirmed.", lines)
        self.assertFalse(any(line.startswith(("Revision", "Objects", "The saved plan")) for line in lines))

    def test_read_changes_nothing(self) -> None:
        """Both commands leave the manager data and the DayZ root as they were."""
        write_mission(self.mission)
        before = saved_state(self.manager, self.dayz)
        self.assertEqual(self.cli(*SHOW)[0], 0)
        self.assertEqual(self.cli(*INSPECT, "--object", "observer-loot")[0], 0)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_unknown_profile_exits_2(self) -> None:
        """A profile that is not named by any profile is an argument error."""
        code, stdout, stderr = self.cli("map", "show", "--profile", "nobody")
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("nobody", stderr)


if __name__ == "__main__":
    unittest.main()
