"""Leaf 10.15: `map save` through the runner on real compositions (parser, input file, output, exit codes)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import FakeTerminal  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402
from map_cli_fixtures import PROFILE_ID, SHOW, MapFixture  # noqa: E402

from dayz_serverman.cli import help_wording, map_input  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.output import CliFailure  # noqa: E402
from dayz_serverman.cli.parser import leaf_help  # noqa: E402
from dayz_serverman.cli.registry import Confirm, Kind, ProfileRule  # noqa: E402
from dayz_serverman.domain.mission_map_values import MAX_DOCUMENT_BYTES  # noqa: E402

SAVE = ["map", "save", "--profile", PROFILE_ID]


class MapPlanSpecTests(unittest.TestCase):
    """The specs, help texts and parser of `map save` and `map set-aside`."""

    def test_specs_are_owner_commands_of_phase_10(self) -> None:
        """Both are WRITE commands that need a profile; save asks nothing, set-aside confirms (PO-5, D14)."""
        table = {command.name: command for command in COMMANDS}
        for name, confirm, methods in (
                ("map save", Confirm.NONE, {"load_mission_map", "save_mission_map_plan"}),
                ("map set-aside", Confirm.CONFIRM, {"load_mission_map", "set_aside_mission_map_plan"})):
            with self.subTest(command=name):
                command = table[name]
                self.assertEqual((command.kind, command.phase, command.profile, command.confirm, command.pending),
                                 (Kind.WRITE, 10, ProfileRule.WRITE_REQUIRED, confirm, False))
                self.assertEqual(command.bridge_methods, methods)

    def test_help_texts_are_the_product_owner_texts(self) -> None:
        """The two commands and the two new destinations use the texts of evidence 10.1."""
        self.assertEqual(help_wording.COMMAND_TEXTS["map save"],
                         "Save a map plan from a file over the saved map plan.")
        self.assertEqual(help_wording.COMMAND_TEXTS["map set-aside"], "Keep an invalid saved map plan under a dated "
                         "name and start a new, empty plan, after a review.")
        self.assertEqual(help_wording.OPTION_TEXTS["plan_file"], "A UTF-8 JSON file that holds the map plan.")
        self.assertEqual(help_wording.OPTION_TEXTS["plan_revision"],
                         "Refuse when the saved map plan is not at this revision.")
        table = {command.name: command for command in COMMANDS}
        page = leaf_help(table["map save"])
        self.assertIn("--from-file FILE", page)
        self.assertIn("A UTF-8 JSON file that holds the map plan.", page)
        self.assertIn("--expect-revision N", page)
        self.assertNotIn("--yes", page)
        self.assertIn("--yes", leaf_help(table["map set-aside"]))


class MapSaveTests(MapFixture):
    """`map save` on the fixture profile: the round trip, the pin, the input file and the refusals."""

    def setUp(self) -> None:
        """Keep a folder for the input files outside the manager and DayZ roots."""
        super().setUp()
        self.inputs = self.manager.parent / "inputs"
        self.inputs.mkdir()

    def shown(self) -> dict:
        """Return the JSON value of `map show`."""
        code, stdout, stderr = self.cli(*SHOW, "--json")
        self.assertEqual(code, 0, stderr)
        return json.loads(stdout)["value"]

    def input_file(self, content: bytes | str | dict, name: str = "plan.json") -> str:
        """Write one input file and return its path as the operator types it."""
        path = self.inputs / name
        if isinstance(content, dict):
            content = json.dumps(content)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return str(path)

    def test_round_trip_asks_nothing_and_saves_the_next_revision(self) -> None:
        """The plan of map show --json, changed, saves without a question or --yes; map show shows the change."""
        loaded = self.shown()
        plan = dict(loaded["plan"], global_abundance=50)
        stdin = FakeTerminal("n\n", terminal=False)
        code, stdout, stderr = self.cli(*SAVE, "--from-file", self.input_file(plan), stdin=stdin)
        self.assertEqual((code, stderr, stdin.reads), (0, "", 0))
        self.assertEqual(stdout.splitlines(), [f"Map plan saved as revision {loaded['revision'] + 1}."])
        again = self.shown()
        self.assertEqual((again["revision"], again["plan"]["global_abundance"]), (loaded["revision"] + 1, 50))
        code, stdout, _stderr = self.cli(*SAVE, "--from-file", self.input_file(plan), "--json")
        document = json.loads(stdout)
        self.assertEqual((code, document["command"], document["success"]), (0, "map save", True))
        self.assertEqual((document["value"]["operations"], document["value"]["review"]), ([], None))
        self.assertEqual(document["value"]["result"]["revision"], loaded["revision"] + 2)
        self.assertEqual(document["value"]["result"]["association"]["profile_id"], PROFILE_ID)

    def test_a_missing_plan_saves_as_revision_0(self) -> None:
        """Without a saved plan the read has no revision, and the first save gives revision 0."""
        path = self.input_file(self.shown()["plan"])
        self.plan_file().unlink()
        code, stdout, stderr = self.cli(*SAVE, "--from-file", path)
        self.assertEqual((code, stdout.splitlines()), (0, ["Map plan saved as revision 0."]), stderr)

    def test_a_pin_that_differs_exits_3_and_a_matching_pin_saves(self) -> None:
        """QF-45: --expect-revision pins the read; another number is REVISION_CONFLICT before the save."""
        loaded = self.shown()
        path = self.input_file(loaded["plan"])
        before = saved_state(self.manager, self.dayz)
        pinned = loaded["revision"] + 5
        code, _stdout, stderr = self.cli(*SAVE, "--from-file", path, "--expect-revision", str(pinned))
        self.assertEqual(code, 3)
        self.assertEqual(stderr.strip(), f"The map plan revision is now {loaded['revision']}, not {pinned}. Read it "
                                         "again with map show --json, then pin the new number.")
        code, stdout, _stderr = self.cli(*SAVE, "--from-file", path, "--expect-revision", str(pinned), "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]),
                         (3, "REVISION_CONFLICT", {"revision": "map plan", "expected": pinned,
                                                   "current": loaded["revision"]}))
        self.assertEqual(saved_state(self.manager, self.dayz), before)
        code, _stdout, stderr = self.cli(*SAVE, "--from-file", path, "--expect-revision", str(loaded["revision"]))
        self.assertEqual(code, 0, stderr)

    def test_a_pin_on_a_missing_plan_names_the_missing_plan(self) -> None:
        """A missing plan has no revision: the pin refuses with the Product Owner text, exit 3, nothing saved."""
        path = self.input_file(self.shown()["plan"])
        self.plan_file().unlink()
        before = saved_state(self.manager, self.dayz)
        code, _stdout, stderr = self.cli(*SAVE, "--from-file", path, "--expect-revision", "0")
        self.assertEqual(code, 3)
        self.assertEqual(stderr.strip(), "There is no saved map plan, so revision 0 does not exist. Nothing was "
                                         "changed. Run map show for the state of the plan.")
        code, stdout, _stderr = self.cli(*SAVE, "--from-file", path, "--expect-revision", "0", "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]),
                         (3, "REVISION_CONFLICT", {"revision": "map plan", "expected": 0, "current": None}))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_command_line_errors_exit_2(self) -> None:
        """No --from-file, a negative pin and a pin that is not a number are parser errors."""
        path = self.input_file(self.shown()["plan"])
        for arguments in ((), ("--from-file", path, "--expect-revision", "-1"),
                          ("--from-file", path, "--expect-revision", "x")):
            with self.subTest(arguments=arguments):
                code, stdout, _stderr = self.cli(*SAVE, *arguments)
                self.assertEqual((code, stdout), (2, ""))

    def test_input_files_that_cannot_be_used_exit_2(self) -> None:
        """Each case has its Product Owner text (evidence 10.15, 16:25:27); exit 2 and nothing changed."""
        before = saved_state(self.manager, self.dayz)
        unreadable = "cannot be read. Check the path and the file."
        not_json = "is not valid JSON. A repeated key, NaN and infinity are not allowed."
        cases = [(str(self.inputs / "missing.json"), unreadable), (str(self.inputs), unreadable),
                 (self.input_file(b"\xff\xfe{}", "bytes.json"), "is not UTF-8 text."),
                 (self.input_file("[1]", "list.json"), "is not a JSON object."),
                 (self.input_file('{"a": 1, "a": 2}', "twice.json"), not_json),
                 (self.input_file('{"a": NaN}', "nan.json"), not_json),
                 (self.input_file('{"a": Infinity}', "infinity.json"), not_json),
                 (self.input_file("{", "broken.json"), not_json),
                 (self.input_file(b" " * (MAX_DOCUMENT_BYTES - 1) + b"{}", "large.json"), "is larger than 8 MiB.")]
        for path, text in cases:
            with self.subTest(path=Path(path).name):
                code, stdout, stderr = self.cli(*SAVE, "--from-file", path)
                self.assertEqual((code, stdout), (2, ""))
                self.assertEqual(stderr.strip(), f"The file {path} {text}")
                code, stdout, _stderr = self.cli(*SAVE, "--from-file", path, "--json")
                self.assertEqual((code, json.loads(stdout)["error"]["code"]), (2, "USAGE"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_the_read_stops_after_the_limit_plus_one_byte(self) -> None:
        """DC-6: a file of exactly the limit is read; one byte more is refused (limit made small for the test)."""
        with patch.object(map_input, "MAX_DOCUMENT_BYTES", 10):
            self.assertEqual(map_input.read_input_text(self.input_file('{"a": 123}', "ten.json")), '{"a": 123}')
            self.assertEqual(map_input.read_plan_object(self.input_file('{"a": 123}', "ten.json")), {"a": 123})
            with self.assertRaises(CliFailure) as raised:
                map_input.read_input_text(self.input_file('{"a": 1234}', "eleven.json"))
        self.assertEqual((raised.exception.code, raised.exception.exit_code), ("USAGE", 2))
        self.assertTrue(str(raised.exception).endswith("eleven.json is larger than 8 MiB."))

    def test_a_document_that_fails_d1_exits_2(self) -> None:
        """The backend D1 check: an unknown field and an image path are invalid_document (INVALID_REQUEST)."""
        plan = self.shown()["plan"]
        before = saved_state(self.manager, self.dayz)
        for document in ({**plan, "extra": 1},
                         {**plan, "background": {"file_name": "map.png", "sha256": "0" * 64, "byte_size": 10,
                                                 "pixel_width": 64, "pixel_height": 64, "path": "C:\\maps\\map.png",
                                                 "calibration": {"a": 1.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": -1.0,
                                                                 "f": 64.0}}}):
            with self.subTest(fields=sorted(set(document) ^ set(plan)) or "background"):
                code, stdout, _stderr = self.cli(*SAVE, "--from-file", self.input_file(document), "--json")
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"], error["details"]["reason"]),
                                 (2, "INVALID_REQUEST", "invalid_document"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_invalid_saved_data_exits_3_and_names_map_set_aside(self) -> None:
        """A damaged plan file is not replaced: the window text and the CLI line of map show, exit 3."""
        path = self.input_file(self.shown()["plan"])
        self.plan_file().write_text("{", encoding="utf-8")
        before = saved_state(self.manager, self.dayz)
        code, _stdout, stderr = self.cli(*SAVE, "--from-file", path)
        self.assertEqual(code, 3)
        self.assertEqual(stderr.strip(), "Read only: the saved map plan is not valid. Set aside invalid plan keeps "
                                         "it under a dated name and starts a new, empty plan. Run map set-aside. Nothing "
                                         "was changed.")
        code, stdout, _stderr = self.cli(*SAVE, "--from-file", path, "--json")
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["reason"]),
                         (3, "REVISION_CONFLICT", "invalid_saved_data"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)


if __name__ == "__main__":
    unittest.main()
