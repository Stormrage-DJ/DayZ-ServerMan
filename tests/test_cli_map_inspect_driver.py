"""Leaf 10.4, design D15: the Inspect driver of `map inspect` (continue calls, progress, restarts, Ctrl+C)."""

from __future__ import annotations

import io
import json
import signal
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from edit_cli_fixtures import PROFILE, EditRoot  # noqa: E402
from mission_map_inspection_support import write_mission  # noqa: E402

from dayz_serverman import session_observer  # noqa: E402
from dayz_serverman.cli import map_inspect_driver, runner  # noqa: E402
from dayz_serverman.cli.bridge_client import CliBridgeError  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CliFailure, Output, line_text  # noqa: E402

TARGET = {"point": {"x": 1.0, "z": 2.0}}
RESULT = {"x": 1.0, "z": 2.0}


def running(phase: str = "reading", done: int | None = None, total: int | None = None) -> dict:
    """Return a running answer with the job identity."""
    return {"status": "running", "job": {"id": "job-1", "phase": phase, "rows_done": done, "rows_total": total}}


STALE = {"status": "stale", "job": {"id": "job-1", "ended": True}}
DONE = {"status": "done", "result": RESULT}
LOST = CliBridgeError("continue_mission_map_inspection", {"code": "INVALID_REQUEST", "message": "The read stopped.",
                                                          "details": {"reason": "inspection_unknown"}})


class Script:
    """A bridge call that answers from a list; an exception in the list is raised. It records each call."""

    def __init__(self, *answers: object, on_call=None) -> None:
        """Keep the answers and an optional hook that runs before each answer."""
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []
        self.on_call = on_call

    def __call__(self, method: str, **parameters: object) -> object:
        """Record the call, run the hook, then return or raise the next answer."""
        self.calls.append((method, parameters))
        if self.on_call is not None:
            self.on_call(method)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def methods(self) -> list[str]:
        """Return the method names in call order."""
        return [method for method, _parameters in self.calls]


def drive(script: Script, interrupts: Interrupts | None = None, *, terminal: bool = False):
    """Run the driver on a script; return its result or failure, and the stderr text."""
    output = Output(io.StringIO(), io.StringIO(), json_mode=False)
    context = SimpleNamespace(call=script, output=output, interrupts=interrupts or Interrupts())
    with patch.object(map_inspect_driver, "is_terminal", return_value=terminal):
        try:
            outcome: object = map_inspect_driver.run_inspection(context, "p1", TARGET)
        except CliFailure as failure:
            outcome = failure
    return outcome, output.stderr.getvalue(), output.stdout.getvalue()


class InspectDriverTests(unittest.TestCase):
    """The driver against scripted bridge answers."""

    def test_continue_calls_do_not_sleep(self) -> None:
        """Each continue call is one read slice: no sleep between the calls."""
        script = Script(running(), running("sampling", 1, 4), running("sampling", 3, 4), DONE)
        with patch.object(time, "sleep", side_effect=AssertionError("the driver must not sleep")):
            outcome, _stderr, _stdout = drive(script)
        self.assertEqual(outcome, RESULT)
        self.assertEqual(script.methods, ["inspect_mission_map", *["continue_mission_map_inspection"] * 3])
        self.assertEqual(script.calls[0][1], {"profile_id": "p1", "plan": None, "target": TARGET})
        self.assertEqual(script.calls[1][1], {"profile_id": "p1", "job_id": "job-1"})

    def test_progress_is_one_line_for_each_phase_off_a_terminal(self) -> None:
        """Off a terminal: a line when the phase changes; stdout stays empty."""
        script = Script(running(), running(), running("sampling", 1, 4), running("sampling", 3, 4), running(), DONE)
        outcome, stderr, stdout = drive(script)
        self.assertEqual(outcome, RESULT)
        self.assertEqual(stderr.splitlines(), ["Reading tiers", "Reading tiers, 25%", "Reading tiers"])
        self.assertEqual(stdout, "")

    def test_progress_rewrites_one_line_on_a_terminal(self) -> None:
        """On a terminal: the line is rewritten with a carriage return, with the floor of the percent, at most 99."""
        script = Script(running(), running("sampling", 2, 3), running("sampling", 3, 3), DONE)
        outcome, stderr, _stdout = drive(script, terminal=True)
        self.assertEqual(outcome, RESULT)
        self.assertNotIn("\n", stderr.replace("\r", "").rstrip())
        self.assertIn("Reading tiers, 66%", stderr)
        self.assertIn("Reading tiers, 99%", stderr)
        self.assertNotIn("100%", stderr)

    def test_a_job_without_a_row_count_shows_no_percent(self) -> None:
        """Sampling with null rows (not yet counted) shows the plain text."""
        _outcome, stderr, _stdout = drive(Script(running("sampling", None, None), DONE))
        self.assertEqual(stderr.splitlines(), ["Reading tiers"])

    def test_two_stale_restarts_are_allowed(self) -> None:
        """Two stale answers start the read again; the third answer is the result."""
        script = Script(running(), STALE, running(), STALE, running(), DONE)
        outcome, stderr, _stdout = drive(script)
        self.assertEqual(outcome, RESULT)
        self.assertEqual(script.methods.count("inspect_mission_map"), 3)
        self.assertEqual(stderr.count("The mission files changed. Reading tiers again"), 2)

    def test_third_stale_answer_is_source_changed_with_exit_3(self) -> None:
        """The third stale answer ends the command: SOURCE_CHANGED, reason changed_while_reading, exit 3."""
        script = Script(running(), STALE, running(), STALE, running(), STALE)
        outcome, _stderr, _stdout = drive(script)
        self.assertIsInstance(outcome, CliFailure)
        self.assertEqual((outcome.code, outcome.exit_code, outcome.details), (
            "SOURCE_CHANGED", 3, {"reason": "changed_while_reading"}))
        self.assertEqual(str(outcome), "The mission files changed while the tiers were read. Nothing was changed. "
                         "Run map inspect again.")
        self.assertEqual(script.methods.count("inspect_mission_map"), 3)
        self.assertNotIn("cancel_mission_map_inspection", script.methods)

    def test_a_lost_job_restarts_once(self) -> None:
        """inspection_unknown starts the read again once; the second start may finish."""
        script = Script(running(), LOST, running(), DONE)
        outcome, _stderr, _stdout = drive(script)
        self.assertEqual(outcome, RESULT)
        self.assertEqual(script.methods, ["inspect_mission_map", "continue_mission_map_inspection",
                                          "inspect_mission_map", "continue_mission_map_inspection"])

    def test_a_second_lost_job_exits_1_with_the_window_failure(self) -> None:
        """A second inspection_unknown ends with "The read stopped." and the command to run again; exit 1."""
        script = Script(running(), LOST, running(), LOST)
        outcome, _stderr, _stdout = drive(script)
        self.assertIsInstance(outcome, CliFailure)
        self.assertEqual((outcome.exit_code, outcome.details), (1, {"reason": "inspection_unknown"}))
        self.assertEqual(str(outcome), "The read stopped. Run map inspect again.")
        self.assertEqual(line_text(outcome.message), str(outcome))

    def test_other_refusals_pass_through_unchanged(self) -> None:
        """A refusal that is not a lost job is the bridge error of the command."""
        refusal = CliBridgeError("inspect_mission_map", {"code": "INVALID_REQUEST", "message": "No.",
                                                         "details": {"reason": "invalid_target"}})
        with self.assertRaises(CliBridgeError) as caught:
            drive(Script(refusal))
        self.assertIs(caught.exception, refusal)

    def test_ctrl_c_cancels_the_job_and_exits_5(self) -> None:
        """Ctrl+C during a read: cancel_mission_map_inspection is called with the job, and the exit is 5."""
        interrupts = Interrupts()
        script = Script(running(), {"cancelled": True},
                        on_call=lambda method: interrupts.handle(signal.SIGINT, None)
                        if method == "inspect_mission_map" else None)
        outcome, stderr, stdout = drive(script, interrupts)
        self.assertIsInstance(outcome, CliFailure)
        self.assertEqual((outcome.code, outcome.exit_code), ("CANCELLED", 5))
        self.assertEqual(str(outcome), "Cancelled. Nothing was changed.")
        self.assertEqual(script.calls[-1], ("cancel_mission_map_inspection", {"profile_id": "p1", "job_id": "job-1"}))
        self.assertEqual(stdout, "")
        self.assertFalse(interrupts.requested)
        self.assertIn("Reading tiers", stderr)

    def test_a_refused_cancel_still_exits_5(self) -> None:
        """The cancel answer does not change the exit code."""
        interrupts = Interrupts()
        refused = CliBridgeError("cancel_mission_map_inspection", {"code": "INVALID_REQUEST", "message": "x",
                                                                   "details": {"reason": "inspection_unknown"}})
        script = Script(running(), refused,
                        on_call=lambda method: interrupts.handle(signal.SIGINT, None) if method ==
                        "inspect_mission_map" else None)
        outcome, _stderr, _stdout = drive(script, interrupts)
        self.assertEqual(outcome.exit_code, 5)


class InspectRunnerCancelTests(EditRoot):
    """Ctrl+C through the whole runner: exit 5, the job cancelled in the backend, nothing on stdout."""

    def test_ctrl_c_during_a_real_read_exits_5(self) -> None:
        """The first call returns running; Ctrl+C is pressed during it; the driver cancels the live job."""
        write_mission(self.mission)
        interrupts = Interrupts()
        real = session_observer.ObserverSession.call
        made: list[str] = []

        def call(session, name, parameters=None):
            """Press Ctrl+C during the start call, then let it finish."""
            made.append(name)
            if name == "inspect_mission_map":
                interrupts.handle(signal.SIGINT, None)
            return real(session, name, parameters)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(session_observer.ObserverSession, "call", call):
            code = runner.run(["map", "inspect", *PROFILE, "--object", "observer-loot"], self.manager, stdout=stdout,
                              stderr=stderr, interrupts=interrupts)
        self.assertEqual((code, stdout.getvalue()), (5, ""))
        self.assertIn("Cancelled. Nothing was changed.", stderr.getvalue())
        self.assertEqual(made[-1], "cancel_mission_map_inspection")

    def test_json_failure_of_a_third_stale_answer(self) -> None:
        """The JSON document of the refusal carries the code, the reason and exit code 3."""
        write_mission(self.mission)
        real = session_observer.ObserverSession.call
        stale = {"status": "stale", "job": {"id": "x", "ended": True}}

        def call(session, name, parameters=None):
            """Answer every start with a stale job; other reads are real."""
            return stale if name == "inspect_mission_map" else real(session, name, parameters)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(session_observer.ObserverSession, "call", call):
            code = runner.run(["map", "inspect", *PROFILE, "--point", "1,1", "--json"], self.manager, stdout=stdout,
                              stderr=stderr, interrupts=Interrupts())
        error = json.loads(stdout.getvalue())["error"]
        self.assertEqual((code, error["code"], error["details"]["reason"]), (3, "SOURCE_CHANGED",
                                                                              "changed_while_reading"))
        self.assertEqual(stderr.getvalue().count("The mission files changed. Reading tiers again"), 2)


if __name__ == "__main__":
    unittest.main()
