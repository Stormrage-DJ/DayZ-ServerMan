"""Leaf 10.15: `map set-aside`, the state refusals of both plan commands, their window texts and input names."""

from __future__ import annotations

import io
import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import FakeTerminal  # noqa: E402
from lifecycle_cli_fixtures import saved_state  # noqa: E402
from map_cli_fixtures import PROFILE_ID, SHOW, MapFixture  # noqa: E402
from test_cli_input_names import InputNameChecker, RecordingOutput, flags  # noqa: E402
from test_cli_map_wording import literal, source  # noqa: E402
from test_mission_map_plan import stored_plan  # noqa: E402

from dayz_serverman.cli import map_wording as words  # noqa: E402
from dayz_serverman.cli import runner  # noqa: E402
from dayz_serverman.cli.bridge_client import CliBridgeError  # noqa: E402
from dayz_serverman.cli.commands import map_plan  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CliFailure, line_text  # noqa: E402
from dayz_serverman.composition import build_composition  # noqa: E402
from dayz_serverman.repositories.mission_map_layout import TargetClass, association_folder, target_key  # noqa: E402
from dayz_serverman.repositories.mission_map_plans import MissionMapPlanStore  # noqa: E402

SET_ASIDE = ["map", "set-aside", "--profile", PROFILE_ID]
STALE = ("The saved map plan changed after it was read. Nothing was changed. Run map show for its current state, "
         "then run the command again.")
NOT_INVALID = ("Not set aside: The saved map plan is valid now, or it is no longer there. Reload plan shows its "
               "current state. Run map show.")


class SetAsideTests(MapFixture):
    """`map set-aside` on the fixture profile: review, question, `--yes`, JSON and the state refusals."""

    def damaged(self) -> Path:
        """Damage the saved plan file, so the load reports invalid saved data; return the plan folder."""
        self.plan_file().write_text("{", encoding="utf-8")
        return self.plan_file().parent

    def path_changed(self) -> None:
        """Save a plan in the folder of an earlier mission path with the same mission root: a path change."""
        composition = build_composition(self.manager)
        try:
            association = composition.mission_map.plans.active_association(PROFILE_ID)
        finally:
            composition.shutdown.request_shutdown()
            composition.shutdown.wait_for_close(5)
        key = target_key(TargetClass.MISSION, self.manager.parent / "earlier" / "mpmissions" / "dayzOffline.enoch")
        plan = dict(stored_plan([]), association={**association, "mission_key": key}, background=None)
        MissionMapPlanStore(association_folder(self.manager / "data" / "mission-map", PROFILE_ID, key)).save(
            plan, None, datetime(2026, 10, 10, 13, 0, 0, tzinfo=timezone.utc))

    def test_review_question_and_yes(self) -> None:
        """The detail and the window message come before the question; "y" or --yes keeps the file renamed."""
        folder = self.damaged()
        stdin = FakeTerminal("y\n")
        code, stdout, stderr = self.cli(*SET_ASIDE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (0, 1), stderr)
        lines = stdout.splitlines()
        self.assertIn(words.SET_ASIDE_MESSAGE, lines)
        self.assertLess(lines.index(words.SET_ASIDE_MESSAGE), len(lines) - 1)
        # Product Owner 16:25:27, point 5: the profile and mission lines of map show come first
        self.assertEqual(lines[:2], [self.cli(*SHOW)[1].splitlines()[0], "Mission: dayzOffline.enoch"])
        self.assertTrue(lines[0].startswith("Profile: "))
        self.assertTrue(lines[2].strip())
        self.assertIn("Continue? [y/N]", stderr)
        kept = [path.name for path in folder.iterdir() if path.name != "plan.json" and path.name.startswith("plan")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(lines[-1], f"The invalid plan was kept as {kept[0]} in its folder. A new, empty plan is open.")
        self.assertFalse((folder / "plan.json").exists())
        code, stdout, _stderr = self.cli(*SHOW)
        self.assertIn("Plan state: No saved plan", stdout.splitlines())

    def test_yes_without_a_terminal_sets_aside_without_a_question(self) -> None:
        """--yes skips only the question: the review still prints, and stdin is never read."""
        self.damaged()
        stdin = FakeTerminal("n\n", terminal=False)
        code, stdout, stderr = self.cli(*SET_ASIDE, "--yes", stdin=stdin)
        self.assertEqual((code, stdin.reads), (0, 0), stderr)
        self.assertNotIn("Continue?", stderr)
        self.assertIn(words.SET_ASIDE_MESSAGE, stdout.splitlines())
        self.assertTrue(stdout.splitlines()[-1].startswith("The invalid plan was kept as "))

    def test_decline_no_terminal_and_json_without_yes_change_nothing(self) -> None:
        """D14 and 8.1: "n", no terminal and JSON without --yes exit 4; the review is in error.details.review."""
        self.damaged()
        before = saved_state(self.manager, self.dayz)
        stdin = FakeTerminal("n\n")
        code, stdout, stderr = self.cli(*SET_ASIDE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 1))
        self.assertIn(words.SET_ASIDE_MESSAGE, stdout.splitlines())
        self.assertTrue(stderr.rstrip().endswith("Nothing was changed."))
        stdin = FakeTerminal("y\n", terminal=False)
        code, _stdout, stderr = self.cli(*SET_ASIDE, stdin=stdin)
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Run the command again with --yes.", stderr)
        stdin = FakeTerminal("y\n")
        code, stdout, _stderr = self.cli(*SET_ASIDE, "--json", stdin=stdin)
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"], error["details"]["review"]["state"], stdin.reads),
                         (4, "CONFIRMATION_REQUIRED", "CORRUPT", 0))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_json_with_yes_gives_the_review_and_the_answer(self) -> None:
        """D12: operations empty, the load answer as review, the set-aside answer as result."""
        self.damaged()
        code, stdout, stderr = self.cli(*SET_ASIDE, "--json", "--yes")
        self.assertEqual(code, 0, stderr)
        value = json.loads(stdout)["value"]
        self.assertEqual((value["operations"], value["review"]["read_only_reason"]), ([], "invalid_saved_data"))
        self.assertEqual(set(value["result"]), {"association", "set_aside"})

    def test_a_plan_that_is_not_invalid_exits_3_before_the_question(self) -> None:
        """A valid plan and a missing plan: the window text and "Run map show.", exit 3, no question."""
        for step in ((lambda: None), (lambda: self.plan_file().unlink())):
            step()
            before = saved_state(self.manager, self.dayz)
            stdin = FakeTerminal("y\n")
            code, stdout, stderr = self.cli(*SET_ASIDE, stdin=stdin)
            self.assertEqual((code, stdout, stdin.reads, stderr.strip()), (3, "", 0, NOT_INVALID))
            code, stdout, _stderr = self.cli(*SET_ASIDE, "--json", "--yes")
            error = json.loads(stdout)["error"]
            self.assertEqual((code, error["code"], error["details"]), (3, "REVISION_CONFLICT",
                                                                     {"reason": "plan_not_invalid"}))
            self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_the_path_change_state_exits_3_and_names_map_adopt(self) -> None:
        """Only adoption ends a path change: set-aside and save refuse with the read-only text of map show."""
        self.path_changed()
        self.inputs = self.manager.parent / "plan.json"
        self.inputs.write_text(json.dumps(stored_plan([])), encoding="utf-8")
        before = saved_state(self.manager, self.dayz)
        for arguments in (SET_ASIDE, ["map", "save", "--profile", PROFILE_ID, "--from-file", str(self.inputs)]):
            with self.subTest(command=arguments[1]):
                stdin = FakeTerminal("y\n")
                code, _stdout, stderr = self.cli(*arguments, stdin=stdin)
                self.assertEqual((code, stdin.reads), (3, 0))
                self.assertEqual(stderr.strip(),
                                 f"{words.READ_ONLY_TEXTS['mission_path_changed']} Run map adopt. Nothing was changed.")
                code, stdout, _stderr = self.cli(*arguments, "--json", "--yes")
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"], error["details"]["reason"]),
                                 (3, "PROFILE_CONTEXT_MISMATCH", "mission_path_changed"))
        self.assertEqual(saved_state(self.manager, self.dayz), before)

    def test_output_follows_the_input_name_rules(self) -> None:
        """11.3: the review, the success lines and the refusals hold no input name in prose."""
        path = self.manager.parent / "in.json"
        names = flags() | {PROFILE_ID, "map save", "map set-aside"}
        self.damaged()
        runs = (SET_ASIDE + ["--yes"], ["map", "save", "--profile", PROFILE_ID, "--from-file", str(path)])
        for arguments in (*runs, SET_ASIDE):
            path.write_text("[]", encoding="utf-8")
            RecordingOutput.made = []
            with self.subTest(arguments=arguments), patch.object(runner, "Output", RecordingOutput):
                runner.run(arguments, self.manager, stdout=io.StringIO(), stderr=io.StringIO(),
                           interrupts=Interrupts())
                written = [block for output in RecordingOutput.made for block in output.written]
                self.assertTrue(written)
                InputNameChecker(self, names, arguments).blocks(written)


class PlanHandlerTests(unittest.TestCase):
    """Handler rules without a composition: Ctrl+C before the call and the bridge refusals of a state."""

    def context(self, answers: dict, interrupts: Interrupts) -> SimpleNamespace:
        """Return a handler context whose bridge answers from a table and records each call."""
        calls: list[str] = []

        def call(method: str, **_parameters: object) -> object:
            calls.append(method)
            answer = answers[method]
            if isinstance(answer, Exception):
                raise answer
            return answer
        return SimpleNamespace(call=call, calls=calls, profile={"profile_id": PROFILE_ID}, interrupts=interrupts,
                               options=SimpleNamespace(plan_revision=None, yes=True), resolved={"plan": {}})

    def test_ctrl_c_before_the_save_sends_nothing(self) -> None:
        """D15: Ctrl+C during the read exits 5, and save_mission_map_plan is never called."""
        interrupts = Interrupts()
        context = self.context({"load_mission_map": {"read_only": False, "revision": 3}}, interrupts)
        interrupts.handle(2, None)
        with self.assertRaises(CliFailure) as raised:
            map_plan.save(context)
        self.assertEqual((raised.exception.exit_code, context.calls), (5, ["load_mission_map"]))

    def test_a_state_refusal_of_the_bridge_gets_the_words_of_its_state(self) -> None:
        """A refusal at the call keeps its code and details; another reason keeps the bridge failure."""
        cases = (("PROFILE_CONTEXT_MISMATCH", "returned_to_earlier_path",
                  f"{words.READ_ONLY_TEXTS['returned_to_earlier_path']} Run map adopt. Nothing was changed."),
                 ("REVISION_CONFLICT", "invalid_saved_data", None), ("REVISION_CONFLICT", "plan_not_invalid", NOT_INVALID),
                 ("REVISION_CONFLICT", "stale_revision", STALE))
        for code, reason, text in cases:
            with self.subTest(reason=reason):
                error = CliBridgeError("save_mission_map_plan", {"code": code, "message": "x",
                                                                 "details": {"reason": reason, "folders": []}})
                context = self.context({"load_mission_map": {"read_only": False, "revision": 3},
                                        "save_mission_map_plan": error}, Interrupts())
                with self.assertRaises(CliFailure) as raised:
                    map_plan.save(context)
                failure = raised.exception
                self.assertEqual((failure.code, failure.exit_code, failure.details),
                                 (code, 3, {"reason": reason, "folders": []}))
                if text is not None:
                    self.assertEqual(line_text(failure.message), text)
        # A storage failure keeps the bridge wording (Product Owner 16:25:27, point 3)
        storage = CliBridgeError("save_mission_map_plan", {"code": "STORAGE_FAILURE", "message": "x",
                                                           "details": {"reason": "storage_failure"}})
        context = self.context({"load_mission_map": {"read_only": False, "revision": 3},
                                "save_mission_map_plan": storage}, Interrupts())
        with self.assertRaises(CliBridgeError):
            map_plan.save(context)

    def test_set_aside_refuses_another_read_only_state_with_its_text(self) -> None:
        """Point 1: a read-only state other than invalid saved data refuses before the question and the call."""
        context = self.context({"load_mission_map": {"read_only": True, "read_only_reason": "association_mismatch",
                                                     "state": "VALID"}}, Interrupts())
        with self.assertRaises(CliFailure) as raised:
            map_plan.set_aside(context)
        failure = raised.exception
        self.assertEqual((failure.code, failure.exit_code, context.calls),
                         ("PROFILE_CONTEXT_MISMATCH", 3, ["load_mission_map"]))
        self.assertEqual(line_text(failure.message), f"{words.READ_ONLY_TEXTS['association_mismatch']} Run profile "
                                                     "edit to correct the profile, then run map show. Nothing was changed.")


class SetAsideWindowTextTests(unittest.TestCase):
    """PO-6: the set-aside texts are the window texts word for word; the CLI lines are exempt."""

    def test_copies_equal_the_window_file(self) -> None:
        """setAsideMessage, setAsideNotInvalid, setAsideOpen and the template of setAsideDone."""
        editor = source("map_editor_wording.js")
        self.assertEqual(literal(editor, "setAsideMessage"), words.SET_ASIDE_MESSAGE)
        self.assertEqual(literal(editor, "setAsideNotInvalid"), words.SET_ASIDE_NOT_INVALID)
        self.assertEqual(literal(editor, "setAsideOpen"), words.SET_ASIDE_OPEN)
        done = re.findall(r"\bsetAsideDone: \(name\) => `([^`]*)`", editor)
        self.assertEqual([text.replace("${name}", "{name}") for text in done], [words.SET_ASIDE_DONE])
        self.assertEqual((words.SAVED, words.PLAN_SUBJECT), ("Map plan saved as revision {n}.", "map plan"))


if __name__ == "__main__":
    unittest.main()
