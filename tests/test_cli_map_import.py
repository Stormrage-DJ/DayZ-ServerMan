"""Leaf 10.33: `map import` through the runner on real compositions (round trip, input, R21, confirmations, pins)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import ChangingTerminal, FakeTerminal  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402
from map_cli_fixtures import PROFILE_ID, SHOW, MapFixture  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from test_mission_map_plan import circle, stored_plan  # noqa: E402
from tests.test_mission_map_prototype import export as prototype_export  # noqa: E402

from dayz_serverman.cli import help_wording, runner  # noqa: E402
from dayz_serverman.cli.bridge_client import BridgeClient  # noqa: E402
from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402

IMPORT = ["map", "import", "--profile", PROFILE_ID]
CONFIRM_LISTED = ("This import has items that DayZ-ServerMan does not import, or it needs a placement review. "
                  "Nothing was changed. Check the list above, then add --confirm-listed.")


class MapImportSpecTests(unittest.TestCase):
    """The spec and the help texts of `map import`."""

    def test_spec_is_a_reviewed_owner_command_of_phase_10(self) -> None:
        """WRITE, profile required, REVIEW, the two owner methods and the options of D11."""
        command = {command.name: command for command in COMMANDS}["map import"]
        self.assertEqual((command.kind, command.phase, command.profile, command.confirm, command.pending),
                         (Kind.WRITE, 10, ProfileRule.WRITE_REQUIRED, Confirm.REVIEW, False))
        self.assertEqual(command.bridge_methods, {"preview_mission_map_import", "import_mission_map_plan"})
        self.assertEqual([(option.dest, option.flag, option.required) for option in command.options],
                         [("profile", "--profile", False), ("plan_file", "--from-file", True),
                          ("transfer_mode", "--mode", True), ("confirm_listed", "--confirm-listed", False),
                          ("plan_revision", "--expect-revision", False)])

    def test_help_texts_are_the_product_owner_texts(self) -> None:
        """The command and the two new destinations use the texts of evidence 10.1."""
        self.assertEqual(help_wording.COMMAND_TEXTS["map import"], "Replace the saved map plan with a plan from a "
                         "file, or merge the two, after a review.")
        self.assertEqual(help_wording.OPTION_TEXTS["transfer_mode"], "How the plan joins the saved plan: replace or "
                         "merge.")
        self.assertEqual(help_wording.OPTION_TEXTS["confirm_listed"], "Confirm the objects and items that the review "
                         "lists as not imported, and the placement review.")


class MapImportTests(MapFixture):
    """`map import` on the fixture profile (a saved plan of one circle at revision 0)."""

    def setUp(self) -> None:
        """Keep a folder for the input files beside the two roots."""
        super().setUp()
        self.inputs = self.manager.parent / "inputs"
        self.inputs.mkdir()

    def file(self, content: bytes | str | dict, name: str = "plan.json") -> str:
        """Write one input file and return its path."""
        data = json.dumps(content) if isinstance(content, dict) else content
        path = self.inputs / name
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return str(path)

    def exported(self) -> str:
        """Export the saved plan with `map export` and return the file path."""
        path = self.inputs / "exported.json"
        path.unlink(missing_ok=True)
        code, _stdout, stderr = self.cli("map", "export", "--profile", PROFILE_ID, "--output", str(path))
        self.assertEqual(code, 0, stderr)
        return str(path)

    def shown(self) -> dict:
        """Return the JSON value of `map show`."""
        code, stdout, stderr = self.cli(*SHOW, "--json")
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)["value"]

    def test_round_trip_with_the_export_of_10_6(self) -> None:
        """Replace with the exported file gives revision 1 and the same export text; JSON has the D12 form."""
        path = self.exported()
        before = Path(path).read_bytes()
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "replace", "--yes")
        self.assertEqual(code, 0, stderr)
        lines = stdout.splitlines()
        self.assertEqual(lines[-1], "Map plan imported as revision 1.")
        for line in (words.TRANSFER_HEADING, words.TERRAIN_SAME, words.BOUNDS_SAME, "Objects in the file: 1",
                     "Objects that get a new ID: 0", words.INSERTIONS["replace"], "Global container capacity: 0%.",
                     "The plan will have 1 object and 0 polygon vertices."):
            self.assertIn(line, lines)
        self.assertNotIn(words.PLACEMENT_REVIEW, lines)
        self.assertEqual(Path(self.exported()).read_bytes(), before)
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "replace", "--yes", "--json")
        value = json.loads(stdout)["value"]
        self.assertEqual((code, value["operations"], value["result"]["revision"]), (0, [], 2), stderr)
        self.assertEqual((value["review"]["destination_revision"], set(value["result"])), (1, {"revision", "report"}))

    def test_merge_puts_the_file_on_top_with_a_new_id_for_a_collision(self) -> None:
        """The colliding ID is listed in the review; the plan then has both objects, the file's first."""
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", self.exported(), "--mode", "merge", "--yes")
        self.assertEqual(code, 0, stderr)
        lines = stdout.splitlines()
        self.assertIn("Objects that get a new ID: 1", lines)
        self.assertIn("  observer-loot", lines)
        self.assertIn(words.INSERTIONS["top"], lines)
        objects = self.shown()["plan"]["objects"]
        self.assertEqual((len(objects), objects[1]["id"]), (2, "observer-loot"))
        self.assertNotEqual(objects[0]["id"], "observer-loot")

    def test_input_that_cannot_be_used_exits_2_and_changes_nothing(self) -> None:
        """Mode, file read, UTF-8, JSON and D1 refusals; the mode is checked before the file is read."""
        before = saved_state(self.manager, self.dayz)
        missing = str(self.inputs / "missing.json")
        cases = [(["--from-file", missing, "--mode", "sideways"], "Choose --mode replace or --mode merge."),
                 (["--from-file", missing, "--mode", "merge"], words.FILE_UNREADABLE.format(path=missing)),
                 (["--from-file", self.file(b"\xff\xfe", "bad.json"), "--mode", "merge"],
                  words.FILE_NOT_UTF8.format(path=self.inputs / "bad.json")),
                 (["--from-file", self.file("{", "broken.json"), "--mode", "merge"],
                  "The file is not a valid map plan."),
                 (["--from-file", self.file({"format": "other"}, "other.json"), "--mode", "merge"],
                  "The file is not a valid map plan.")]
        for arguments, text in cases:
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.cli(*IMPORT, *arguments, "--yes")
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn(text, stderr)
        code, stdout, _stderr = self.cli(*IMPORT, "--from-file", str(self.inputs / "other.json"), "--mode", "merge",
                                         "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["details"]["reason"]), (2, "invalid_document"))
        self.assertTrue(error["details"]["problems"])
        self.assertEqual(self.cli(*IMPORT, "--from-file", missing)[0], 2)
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_listed_objects_need_confirm_listed_also_with_yes(self) -> None:
        """An object outside the bounds: exit 4 after the review; with --confirm-listed it is left out."""
        path = self.file(stored_plan([circle("inside"), circle("far", x=5000.0)]))
        before = saved_state(self.manager, self.dayz)
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "replace", "--yes")
        self.assertEqual(code, 4)
        self.assertIn(words.EXCLUDED_HEADING, stdout.splitlines())
        self.assertIn(CONFIRM_LISTED, stderr)
        code, stdout, _stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "replace", "--yes", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["excluded_count"]),
                         (4, "CONFIRMATION_REQUIRED", 1))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "replace", "--confirm-listed",
                                        "--yes")
        self.assertEqual((code, stdout.splitlines()[-1]), (0, "Map plan imported as revision 1. 1 object was left "
                                                             "out."), stderr)
        self.assertEqual([item["id"] for item in self.shown()["plan"]["objects"]], ["inside"])

    def test_a_pripyat_prototype_export_needs_confirm_listed(self) -> None:
        """R21: the unmapped items and the placement review are listed; --yes alone exits 4."""
        path = self.file(prototype_export(), "prototype.json")
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", "--yes")
        lines = stdout.splitlines()
        self.assertEqual(code, 4, stderr)
        for line in (words.UNMAPPED_HEADING, words.PLACEMENT_REVIEW, "Terrain: Pripyat in the file, test.terrain in "
                     "this mission"):
            self.assertIn(line, lines)
        self.assertTrue(any(line.startswith("  P101: ") for line in lines))
        code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", "--confirm-listed", "--yes")
        self.assertEqual(code, 0, stderr)
        self.assertTrue(stdout.splitlines()[-1].startswith("Map plan imported as revision 1."))
        self.assertIn("Z101", [item["id"] for item in self.shown()["plan"]["objects"]])

    def test_the_question_and_yes(self) -> None:
        """No terminal without --yes exits 4; "n" exits 4; "y" imports; --confirm-listed never answers the question."""
        path = self.exported()
        before = saved_state(self.manager, self.dayz)
        for stdin, reads in ((FakeTerminal("y\n", terminal=False), 0), (FakeTerminal("n\n"), 1)):
            with self.subTest(reads=reads):
                code, _stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", "--confirm-listed",
                                                 stdin=stdin)
                self.assertEqual((code, stdin.reads), (4, reads), stderr)
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        stdin = FakeTerminal("y\n")
        sent: list[dict] = []
        original = BridgeClient.call

        def recorded(client, method, **parameters):
            """Keep the parameters of the import call, then dispatch it."""
            if method == "import_mission_map_plan":
                sent.append(parameters)
            return original(client, method, **parameters)

        with patch.object(BridgeClient, "call", recorded):
            code, stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", stdin=stdin)
        self.assertEqual((code, stdin.reads, stdout.splitlines()[-1]), (0, 1, "Map plan imported as revision 1."),
                         stderr)
        # D14: a report that needs no confirmation of listed items is sent with `confirmed` false, at its revision
        self.assertEqual([(item["confirmed"], item["expected_revision"]) for item in sent], [(False, 0)])

    def test_stale_revision_and_pins(self) -> None:
        """A pin that differs exits 3 before the question; a save during the question exits 3 with the 10.15 text."""
        path = self.exported()
        code, stdout, _stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", "--yes",
                                         "--expect-revision", "5", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["expected"]), (3, "REVISION_CONFLICT", 5))
        # Another save while the question waits: the import sends revision 0 and the plan is at revision 1
        stdin = ChangingTerminal(lambda: self.save_objects([circle("changed")]))
        code, _stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", stdin=stdin)
        self.assertEqual(code, 3, stderr)
        self.assertIn(words.STALE_PLAN, stderr)
        self.assertEqual([item["id"] for item in self.shown()["plan"]["objects"]], ["changed"])
        code, _stdout, stderr = self.cli(*IMPORT, "--from-file", path, "--mode", "merge", "--yes",
                                         "--expect-revision", "1")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.shown()["revision"], 2)

    def test_no_valid_plan_and_a_plan_limit_exit_3(self) -> None:
        """A corrupt saved plan, and a merge past 2,000 objects, refuse before the question."""
        many = self.file(stored_plan([circle(f"c{index}") for index in range(2000)]), "many.json")
        code, _stdout, stderr = self.cli(*IMPORT, "--from-file", many, "--mode", "merge", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("The result would hold 2001 objects and 0 polygon vertices. The limits are 2,000 objects and "
                      "50,000 vertices. Nothing was changed.", stderr)
        # D14: the refusal comes before the question, so a terminal is never asked
        stdin = FakeTerminal("y\n")
        code, _stdout, stderr = self.cli(*IMPORT, "--from-file", many, "--mode", "merge", stdin=stdin)
        self.assertEqual((code, stdin.reads), (3, 0), stderr)
        self.plan_file().write_bytes(b"{")
        code, _stdout, stderr = self.cli(*IMPORT, "--from-file", many, "--mode", "replace", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("This profile has no valid saved map plan. Nothing was changed. Run map show for the state of "
                      "the plan.", stderr)

    def test_output_follows_the_input_name_rules(self) -> None:
        """11.3: the review values are data, the command words are text to type, the typed paths are echoes."""
        RecordingOutput.made.clear()
        listed = self.file(prototype_export(), "prototype.json")
        exported = self.exported()
        original = runner.Output
        runner.Output = RecordingOutput  # type: ignore[misc]
        try:
            self.cli(*IMPORT, "--from-file", listed, "--mode", "merge", "--yes")
            self.cli(*IMPORT, "--from-file", listed, "--mode", "sideways")
            self.cli(*IMPORT, "--from-file", exported, "--mode", "merge", "--yes")
        finally:
            runner.Output = original  # type: ignore[misc]
        checker = InputNameChecker(self, flags() | {PROFILE_ID}, [listed, exported])
        for output in RecordingOutput.made:
            checker.blocks(output.written)


if __name__ == "__main__":
    unittest.main()
