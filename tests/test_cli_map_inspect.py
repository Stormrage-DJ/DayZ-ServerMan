"""Leaf 10.4: `map inspect` through the runner on real compositions (parser, output, exit codes)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from map_cli_fixtures import INSPECT, PROFILE_ID, MapFixture  # noqa: E402
from mission_map_inspection_support import write_mission  # noqa: E402
from tests.mission_map_mapgroup_support import DIRTS, POSITIONS, PROTOTYPE, zone  # noqa: E402
from test_mission_map_plan import circle, encounter  # noqa: E402

from dayz_serverman.domain.mission_map_loot_report_texts import CAPACITY_REASON_TEXTS  # noqa: E402

class MapInspectTests(MapFixture):
    """`map inspect` on the fixture: both targets, the JSON form, progress on stderr and the refusals."""

    def test_point_answer_in_text(self) -> None:
        """The point answer shows place, tiers with their source, the winning zone and the point capacity sentence."""
        write_mission(self.mission)
        code, stdout, _stderr = self.cli(*INSPECT, "--point", "100,100")
        lines = stdout.splitlines()
        self.assertEqual(code, 0)
        self.assertIn("Point: X 100 m, Z 100 m", lines)
        self.assertIn("Grid: X 1, Z 1", lines)
        self.assertIn("Cell: Column 3, row 3", lines)
        self.assertIn("Original: Tier3, Tier4", lines)
        self.assertIn("Effective tier: Tier3, Tier4 (original)", lines)
        self.assertIn("Set by: Loot circle 1. It changes only the capacity. Its tier is Inherit.", lines)
        self.assertIn("Capacity change: global 0%, zone Rich +50%, together +50%.", lines)
        self.assertIn("Encounters that cover this point", lines)
        self.assertEqual(lines[-1], CAPACITY_REASON_TEXTS["point_target"])

    def test_point_outside_every_zone_is_set_by_the_original_tier(self) -> None:
        """No winner: the Product Owner text for the original tier, and no zone in the capacity line."""
        write_mission(self.mission)
        _code, stdout, _stderr = self.cli(*INSPECT, "--point", "900,900")
        self.assertIn("Set by: The original tier", stdout.splitlines())
        self.assertIn("Capacity change: global 0%, no zone, together 0%.", stdout.splitlines())

    def test_covering_encounters_come_from_the_answer(self) -> None:
        """The encounters of the backend answer are listed; the CLI does not test them again."""
        write_mission(self.mission)
        self.save_objects([circle("c1"), encounter("e1")])
        _code, stdout, _stderr = self.cli(*INSPECT, "--point", "500,500")
        lines = stdout.splitlines()
        self.assertEqual(lines[lines.index("Encounters that cover this point") + 1], "  Encounter 1")

    def test_object_answer_in_text_and_progress_on_stderr(self) -> None:
        """The object answer lists tiers, cells and building groups; progress is on stderr, not on stdout."""
        write_mission(self.mission)
        code, stdout, stderr = self.cli(*INSPECT, "--object", "observer-loot")
        lines = stdout.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[1:], ["Object: observer-loot", "Effective tier: Tier1, Tier2, Tier3, Tier4",
                                     "Original: Tier1, Tier2, Tier3, Tier4", "Cells: 8 (a cell counts when its centre is inside the object)", "Tier writable: Yes",
                                     "Building groups:", "  This zone covers no buildings."])
        self.assertIn("Reading tiers", stderr.splitlines())
        self.assertNotIn("Reading tiers", stdout)

    def test_object_answer_lists_the_building_groups(self) -> None:
        """The 6.9 capacity list prints the window sentences: group, containers, point limits, bound, generated group."""
        write_mission(self.mission)
        for name, data in (("mapgroupproto.xml", PROTOTYPE), ("mapgrouppos.xml", POSITIONS),
                           ("mapgroupdirt.xml", DIRTS)):
            (self.mission / name).write_bytes(data)
        self.save_objects([zone()])
        code, stdout, _stderr = self.cli(*INSPECT, "--object", "zone")
        lines = stdout.splitlines()
        groups = lines[lines.index("Building groups:") + 1:]
        self.assertEqual(code, 0)
        self.assertEqual(len(groups), 3)
        self.assertTrue(groups[0].startswith("  Land_City_Hospital: 1 building at +50%. Group capacity: 4 now, 6 with "
                                             "this plan. Containers: lootFloor 3 to 5, lootShelves 4 to 6. The spawn "
                                             "points of lootFloor allow 2 at most."))
        self.assertIn("Upper bound: the point counts are not proved usable, so fewer items may fit. Apply writes them "
                      "to group ServerManMap_G_", groups[0])
        self.assertIn("This building uses a PlannerRich_ group of an earlier planner tool.", groups[2])
        code, stdout, _stderr = self.cli(*INSPECT, "--object", "zone", "--json")
        entries = json.loads(stdout)["value"]["capacity"]
        self.assertEqual([entry["source"] for entry in entries], ["Land_City_Hospital", "Land_Shed",
                                                                 "PlannerRich_00001"])

    def test_json_is_the_final_answer_with_the_profile(self) -> None:
        """Only the final `done.result` is the value, with `profile_id`; no progress in the document."""
        write_mission(self.mission)
        code, stdout, _stderr = self.cli(*INSPECT, "--point", "100,100", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["profile_id"], value["capacity_reason"]), (0, PROFILE_ID, "point_target"))
        self.assertEqual(value["original_tier"], ["Tier3", "Tier4"])
        self.assertNotIn("status", value)
        code, stdout, _stderr = self.cli(*INSPECT, "--object", "observer-loot", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["object_id"], value["capacity"]), (0, "observer-loot", []))

    def test_target_forms_exit_2(self) -> None:
        """No target, both targets, and every point form that is not two decimals with one comma: exit 2."""
        for arguments in ([], ["--point", "1,2", "--object", "a"], ["--point", "7500"], ["--point", "1, 2"],
                          ["--point", "a,b"], ["--point", "1e3,2"], ["--point", "1,2,3"], ["--point", ","],
                          ["--point", "9" * 400 + ",1"]):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.cli(*INSPECT, *arguments)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn("The command line is not valid", stderr)
        # A value that starts with "-" needs the "=" form, as for every option of the parser
        for point in ("7.5,-2.25", "-5,3"):
            code, stdout, stderr = self.cli(*INSPECT, f"--point={point}", "--json")
            self.assertEqual(code, 0, (stdout, stderr))
            value = json.loads(stdout)["value"]
            self.assertEqual((code, value["x"], value["z"]), (0, *(float(part) for part in point.split(","))))

    def test_usage_messages_are_the_product_owner_texts(self) -> None:
        """None, both, and a bad point each have their own sentence."""
        for arguments, text in (([], "name a point with --point or an object with --object"),
                                (["--point", "1,2", "--object", "a"], "use --point or --object, not both"),
                                (["--point", "7500"], "--point needs two numbers as X,Z in native metres, for "
                                                      "example 7500,7500")):
            with self.subTest(arguments=arguments):
                _code, _stdout, stderr = self.cli(*INSPECT, *arguments)
                self.assertIn(f"The command line is not valid: {text}. Run help map inspect", stderr)

    def test_backend_refusals_keep_their_exit_codes(self) -> None:
        """An unknown object and a point outside the plan exit 2; a mission file that is missing exits 1."""
        write_mission(self.mission)
        code, _stdout, stderr = self.cli(*INSPECT, "--object", "nothing")
        self.assertEqual(code, 2)
        self.assertIn("Inspect needs a point on the map or an object of this plan.", stderr)
        code, stdout, _stderr = self.cli(*INSPECT, "--object", "nothing", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["reason"]), (2, "INVALID_REQUEST", "invalid_target"))
        (self.mission / "areaflags.map").unlink()
        code, _stdout, stderr = self.cli(*INSPECT, "--object", "observer-loot")
        self.assertEqual(code, 1)
        self.assertTrue(stderr.strip())

    def test_invalid_saved_plan_refuses_with_exit_3(self) -> None:
        """Invalid saved data is a refusal of the current state (the existing REVISION_CONFLICT rule)."""
        write_mission(self.mission)
        self.plan_file().write_text("{", encoding="utf-8")
        code, stdout, _stderr = self.cli(*INSPECT, "--point", "1,1", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["reason"]), (3, "REVISION_CONFLICT",
                                                                              "invalid_saved_data"))

    def test_unreadable_header_refuses_with_exit_3(self) -> None:
        """A tier map that is not valid is UNSUPPORTED_SNAPSHOT_CONTENT: exit 3."""
        write_mission(self.mission)
        (self.mission / "areaflags.map").write_bytes(b"short")
        code, stdout, _stderr = self.cli(*INSPECT, "--object", "observer-loot", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["reason"]),
                         (3, "UNSUPPORTED_SNAPSHOT_CONTENT", "tier_header_invalid"))


if __name__ == "__main__":
    unittest.main()
