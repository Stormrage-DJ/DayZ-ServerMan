"""Task 3.2, criteria 10 and 12 (design 7): the waiter's progress, Ctrl+C states and end state, and one flow step."""

from __future__ import annotations

import io
import signal
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli.bridge_client import CliBridgeError  # noqa: E402
from dayz_serverman.cli.flow import run_operation, stop_if_interrupted  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CliFailure, Output, line_text  # noqa: E402
from dayz_serverman.cli.waiter import Waiter, WaitState  # noqa: E402
from dayz_serverman.cli import wording  # noqa: E402


def record(state: str, phase: str = "preflight", percent: int = 10, *, kind: str = "STOP_SERVER",
           error: dict | None = None, last: str | None = None) -> dict:
    """Return an operation record as get_operation answers it."""
    return {"operation_id": "op-1", "kind": kind, "state": state, "progress_phase": phase,
            "progress_percent": percent, "terminal_error": error, "last_working_phase": last or phase}


class ScriptedBridge:
    """Answers get_operation from a script; Ctrl+C presses and cancellation answers are scripted per poll."""

    def __init__(self, records: list[dict], interrupts: Interrupts, *, presses: set[int] = frozenset(),
                 cancel_errors: list[str | None] | None = None) -> None:
        """Keep the records (the last one repeats), the polls after which Ctrl+C is pressed, and cancel answers."""
        self.records = records
        self.interrupts = interrupts
        self.presses = presses
        self.cancel_errors = list(cancel_errors or [])
        self.calls: list[str] = []
        self.polls = 0

    def __call__(self, method: str, **parameters: object) -> dict:
        """Answer one bridge call."""
        self.calls.append(method)
        if method == "get_operation":
            answer = self.records[min(self.polls, len(self.records) - 1)]
            if self.polls in self.presses:
                self.interrupts.handle(signal.SIGINT, None)
            self.polls += 1
            return answer
        if method == "request_operation_cancellation":
            code = self.cancel_errors.pop(0) if self.cancel_errors else None
            if code is not None:
                raise CliBridgeError(method, {"code": code, "message": "no safe point"})
            return record("CANCELLING")
        if method == "stop_server":
            return {"operation_id": "op-1", "state": "QUEUED"}
        raise AssertionError(method)


class WaiterTests(unittest.TestCase):
    """Polls until a terminal state; Ctrl+C requests cancellation and never abandons the operation."""

    def setUp(self) -> None:
        """A text output on fake streams, a flag handler and a fake clock."""
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.output = Output(self.stdout, self.stderr, json_mode=False)
        self.interrupts = Interrupts()
        self.now = 0.0

    def waiter(self, bridge: ScriptedBridge, *, terminal: bool = False) -> Waiter:
        """Return a waiter whose sleep moves the fake clock by the poll interval."""
        def sleep(seconds: float) -> None:
            self.now += seconds
        return Waiter(bridge, self.output, self.interrupts, sleep=sleep, clock=lambda: self.now,
                      terminal=terminal, width=lambda: 50, now_text=lambda: "12:00:00")

    def test_ctrl_c_requests_cancellation_and_waits_for_the_end_state(self) -> None:
        """One press: one request, the note, then the cancelled record is returned."""
        bridge = ScriptedBridge([record("RUNNING"), record("CANCELLING"), record("CANCELLED", "cancelled")],
                                self.interrupts, presses={0})
        waiter = self.waiter(bridge)
        ended = waiter.wait("op-1", "STOP_SERVER")
        self.assertEqual(ended["state"], "CANCELLED")
        self.assertEqual(bridge.calls.count("request_operation_cancellation"), 1)
        self.assertEqual(waiter.state, WaitState.CANCEL_REQUESTED)
        self.assertIn(wording.CANCEL_REQUESTED, self.stderr.getvalue())

    def test_a_second_press_while_cancelling_only_says_so(self) -> None:
        """A press after an accepted request sends no second request."""
        bridge = ScriptedBridge([record("RUNNING"), record("CANCELLING"), record("CANCELLING"),
                                 record("CANCELLED", "cancelled")], self.interrupts, presses={0, 1})
        self.waiter(bridge).wait("op-1", "STOP_SERVER")
        self.assertEqual(bridge.calls.count("request_operation_cancellation"), 1)
        self.assertIn(wording.ALREADY_CANCELLING, self.stderr.getvalue())

    def test_not_cancellable_notes_and_keeps_waiting_and_asks_again_later(self) -> None:
        """OPERATION_NOT_CANCELLABLE: the note at most every 2 s, every press asks again, the end state is returned."""
        records = [record("RUNNING")] * 8 + [record("SUCCEEDED", "complete", 100)]
        bridge = ScriptedBridge(records, self.interrupts, presses={0, 1, 6},
                                cancel_errors=["OPERATION_NOT_CANCELLABLE"] * 3)
        waiter = self.waiter(bridge)
        ended = waiter.wait("op-1", "STOP_SERVER")
        self.assertEqual(ended["state"], "SUCCEEDED")
        self.assertEqual(bridge.calls.count("request_operation_cancellation"), 3)
        # Presses at 0 s and 0.5 s give one note; the press at 3 s gives the second
        self.assertEqual(self.stderr.getvalue().count(wording.NOT_CANCELLABLE), 2)
        self.assertEqual(waiter.state, WaitState.NOT_CANCELLABLE)

    def test_end_state_wins_over_a_ctrl_c_request(self) -> None:
        """Ctrl+C pressed, but the operation succeeds: run_operation returns it (exit 0)."""
        bridge = ScriptedBridge([record("RUNNING"), record("SUCCEEDED", "complete", 100)], self.interrupts,
                                presses={0}, cancel_errors=["OPERATION_NOT_CANCELLABLE"])
        context = SimpleNamespace(call=bridge, output=self.output, interrupts=self.interrupts)
        ended = run_operation(context, "stop_server", "STOP_SERVER", waiter=self.waiter(bridge),
                              profile_id="p", expected_profile_revision=0, expected_settings_revision=0,
                              backup_after_stop=False)
        self.assertEqual(ended["state"], "SUCCEEDED")
        self.assertIn("Server stopped.", self.stderr.getvalue())

    def test_cancelled_and_failed_ends_become_the_command_failure(self) -> None:
        """Cancelled: exit 5 with the kind's cancel sentence; a refusal before the marker: 3; after it: 1."""
        cases = (
            (record("CANCELLED", "cancelled", 30), 5, "CANCELLED", "Backup cancelled. The server stays stopped."),
            (record("FAILED", "failed", 20, error={"code": "EXTERNAL_PROCESS", "message": "x"}, last="STOP_SERVER"),
             3, "EXTERNAL_PROCESS", "The server could not be stopped."),
            (record("FAILED", "failed", 21, error={"code": "CONTROL_CONFLICT", "message": "x"}, last="STOP_SERVER"),
             1, "CONTROL_CONFLICT", "The server could not be stopped."),
            (record("RECOVERY_REQUIRED", "failed", 21, error={"code": "PROCESS_STATE_UNKNOWN", "message": "x"}),
             6, "PROCESS_STATE_UNKNOWN", "The server could not be stopped."),
        )
        for ended, exit_code, code, text in cases:
            with self.subTest(code=code):
                bridge = ScriptedBridge([ended], self.interrupts)
                context = SimpleNamespace(call=bridge, output=self.output, interrupts=self.interrupts)
                with self.assertRaises(CliFailure) as raised:
                    run_operation(context, "stop_server", "STOP_SERVER", waiter=self.waiter(bridge))
                failure = raised.exception
                self.assertEqual((failure.exit_code, failure.code), (exit_code, code))
                self.assertTrue(str(failure).startswith(text), str(failure))
                self.assertEqual(failure.details["operation"]["state"], ended["state"])

    def test_ctrl_c_before_the_submit_exits_5_and_submits_nothing(self) -> None:
        """Between two steps of a flow: the next step is not submitted."""
        bridge = ScriptedBridge([record("SUCCEEDED")], self.interrupts)
        self.interrupts.handle(signal.SIGINT, None)
        context = SimpleNamespace(call=bridge, output=self.output, interrupts=self.interrupts)
        with self.assertRaises(CliFailure) as raised:
            run_operation(context, "stop_server", "STOP_SERVER", waiter=self.waiter(bridge))
        self.assertEqual((raised.exception.exit_code, raised.exception.code), (5, "CANCELLED"))
        self.assertEqual(bridge.calls, [])
        stop_if_interrupted(self.interrupts)

    def test_a_refusal_at_dispatch_keeps_its_exit_code(self) -> None:
        """A queue refusal at submit is exit 3 and never waits."""
        def call(method: str, **_parameters: object) -> dict:
            raise CliBridgeError(method, {"code": "MUTATION_CONFLICT", "message": "full",
                                          "details": {"reason": "QUEUE_FULL"}})
        context = SimpleNamespace(call=call, output=self.output, interrupts=self.interrupts)
        with self.assertRaises(CliFailure) as raised:
            run_operation(context, "stop_server", "STOP_SERVER")
        self.assertEqual(raised.exception.exit_code, 3)

    def test_non_terminal_rendering_is_one_line_per_change(self) -> None:
        """Off a terminal: "<time> <kind>: <phase>" per change of state or phase; stdout stays empty."""
        records = [record("QUEUED", "queued", 0), record("RUNNING", "preflight", 10), record("RUNNING", "preflight", 10),
                   record("RUNNING", "STOP_SERVER", 20), record("SUCCEEDED", "complete", 100)]
        self.waiter(ScriptedBridge(records, self.interrupts)).wait("op-1", "STOP_SERVER")
        self.assertEqual(self.stderr.getvalue().splitlines(), [
            "12:00:00 Stopping server: Waiting to start",
            "12:00:00 Stopping server: Checking the server state",
            "12:00:00 Stopping server: Saving the world and stopping the server",
            "Server stopped.",
        ])
        self.assertEqual(self.stdout.getvalue(), "")

    def test_terminal_rendering_rewrites_one_line_with_the_determinate_percent(self) -> None:
        """On a terminal: one line rewritten with a carriage return, cut to the width, cleared at the end."""
        records = [record("RUNNING", "BACKUP_DISCOVER", 30, kind="STOP_SERVER"),
                   record("RUNNING", "STOP_SERVER", 20), record("SUCCEEDED", "complete", 100)]
        self.waiter(ScriptedBridge(records, self.interrupts), terminal=True).wait("op-1", "STOP_SERVER")
        text = self.stderr.getvalue()
        self.assertIn("\rStopping server: Backup: copying files 30%", text)
        # Cut to the width less one, so the line never wraps
        self.assertIn("\rStopping server: Saving the world and stopping th\r", text)
        self.assertNotIn("Saving the world and stopping the server", text)
        self.assertTrue(text.endswith("\rServer stopped.\n"), text)
        self.assertEqual([line_text(block) for block in self.output.written][-1], "Server stopped.")

    def test_sign_in_shows_no_progress_until_the_end(self) -> None:
        """Steam sign-in: the prompt line first, no progress line, then the end sentence."""
        records = [record("RUNNING", "wait_steamcmd", 5, kind="AUTHENTICATE_STEAMCMD")] * 3 + [
            record("SUCCEEDED", "complete", 100, kind="AUTHENTICATE_STEAMCMD")]
        self.waiter(ScriptedBridge(records, self.interrupts), terminal=True).wait("op-1", "AUTHENTICATE_STEAMCMD")
        self.assertEqual(self.stderr.getvalue().splitlines(),
                         [wording.SIGN_IN_PROMPT, "Steam sign-in completed."])


if __name__ == "__main__":
    unittest.main()
