"""Task 2.5, criterion 7: text by default, one versioned JSON document with `--json`, progress on stderr (6.3)."""

from __future__ import annotations

import io
import json
import signal
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application.activity_wording import leaks_identifier  # noqa: E402
from dayz_serverman.cli import main as cli_main, runner  # noqa: E402
from dayz_serverman.cli.bridge_client import CliBridgeError  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import (  # noqa: E402
    CliFailure, CommandResult, Echo, InputColumn, Label, Line, Output, Table, TypeText, block_parts, render_blocks,
    sentence,
)


class _Session:
    """Observer session stand-in: no composition is built for these tests."""

    def close(self) -> None:
        """Nothing to close."""


def run_with(handler, arguments: list[str]) -> tuple[int, str, str]:
    """Run `server status` or another read with a stand-in handler and session; return code and streams."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(runner, "load_handler", return_value=handler), \
            patch.object(runner.observer_sessions, "open_observer_session", return_value=_Session()):
        code = runner.run(arguments, None, stdout=stdout, stderr=stderr, interrupts=Interrupts())
    return code, stdout.getvalue(), stderr.getvalue()


class JsonDocumentTests(unittest.TestCase):
    """The envelope of A11: {cli_version, command, success, value | error{code, message, retryable, details?}}."""

    def test_success_document_shape(self) -> None:
        """Success: exactly the four members, with the raw value."""
        def handler(context):
            """Write a progress note and return a value."""
            context.output.note([sentence("Reading the server state")])
            return CommandResult(value={"state": "STOPPED"}, blocks=[sentence("Stopped")])
        code, stdout, stderr = run_with(handler, ["server", "status", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"cli_version": 1, "command": "server status", "success": True,
                                              "value": {"state": "STOPPED"}})
        # One document on one line; the progress note is on stderr only
        self.assertEqual(stdout.count("\n"), 1)
        self.assertEqual(stderr, "Reading the server state\n")

    def test_failure_document_shape(self) -> None:
        """Failure: error with code, message, retryable and details; no value member."""
        def handler(_context):
            """Fail with a bridge refusal."""
            raise CliBridgeError("get_server_status", {"code": "PROCESS_STATE_UNKNOWN", "message": "inventory failed",
                                                       "retryable": True, "details": {"owner": None}})
        code, stdout, _stderr = run_with(handler, ["server", "status", "--json"])
        document = json.loads(stdout)
        self.assertEqual(code, 3)
        self.assertEqual(set(document), {"cli_version", "command", "success", "error"})
        self.assertEqual(set(document["error"]), {"code", "message", "retryable", "details"})
        self.assertEqual((document["error"]["code"], document["error"]["retryable"]), ("PROCESS_STATE_UNKNOWN", True))
        # The message is operator text, never the raw host text
        self.assertFalse(leaks_identifier(document["error"]["message"]))

    def test_unexpected_failure_is_one_document_with_exit_1(self) -> None:
        """An unexpected exception gives INTERNAL_FAILURE, exit 1, and no traceback on either stream."""
        def handler(_context):
            """Fail unexpectedly."""
            raise RuntimeError("boom")
        code, stdout, stderr = run_with(handler, ["server", "status", "--json"])
        self.assertEqual((code, json.loads(stdout)["error"]["code"]), (1, "INTERNAL_FAILURE"))
        self.assertNotIn("Traceback", stdout + stderr)
        code, stdout, stderr = run_with(handler, ["server", "status"])
        self.assertEqual((code, stdout), (1, ""))
        self.assertEqual(stderr, "Something went wrong inside DayZ-ServerMan.\n")

    def test_non_ascii_text_is_escaped(self) -> None:
        """The document is ASCII, so any console code page can carry it."""
        def handler(_context):
            """Return a name with accents."""
            return CommandResult(value={"name": "Livonia Közösségi"})
        _code, stdout, _stderr = run_with(handler, ["server", "status", "--json"])
        self.assertTrue(stdout.isascii())
        self.assertEqual(json.loads(stdout)["value"]["name"], "Livonia Közösségi")


class TextOutputTests(unittest.TestCase):
    """Text mode: results on stdout, notes and errors on stderr, built from typed parts (11.3)."""

    def test_text_result_and_error_streams(self) -> None:
        """A result goes to stdout; a refusal goes to stderr with its exit code."""
        def ok(_context):
            """Return one line."""
            return CommandResult(value=None, blocks=[sentence("Stopped")])
        self.assertEqual(run_with(ok, ["server", "status"]), (0, "Stopped\n", ""))

        def refused(_context):
            """Refuse with a CLI failure."""
            raise CliFailure("CONTROL_CONFLICT", sentence("Try again in a minute."), 3)
        self.assertEqual(run_with(refused, ["server", "status"]), (3, "", "Try again in a minute.\n"))

    def test_typed_parts_and_tables(self) -> None:
        """Sentences join prose into labels; a table types its input columns; rendering aligns columns."""
        line = sentence("Run ", TypeText("backup list"), " for the IDs of ", Echo("main"), ".")
        self.assertEqual(line.parts, (Label("Run "), TypeText("backup list"), Label(" for the IDs of "),
                                      Echo("main"), Label(".")))
        table = Table(("Profile", "ID"), (("Livonia", "livonia-main"), ("Chernarus PvE", "pve")), frozenset({1}))
        self.assertIn(InputColumn("livonia-main"), block_parts([table]))
        self.assertIn(Label("Chernarus PvE"), block_parts([table]))
        self.assertEqual(render_blocks([table]).splitlines(),
                         ["Profile        ID", "Livonia        livonia-main", "Chernarus PvE  pve"])
        output = Output(io.StringIO(), io.StringIO(), json_mode=False, command="profile list")
        output.success(CommandResult(blocks=[line, Line(())]))
        self.assertEqual(output.written[0], line)



class InterruptTests(unittest.TestCase):
    """6.7: Ctrl+C sets a flag outside a question and raises only while a question waits."""

    def test_flag_outside_a_question(self) -> None:
        """The handler never raises outside a question; `take` returns and clears the request."""
        interrupts = Interrupts()
        interrupts.handle(signal.SIGINT, None)
        self.assertTrue(interrupts.requested)
        self.assertTrue(interrupts.take())
        self.assertFalse(interrupts.requested)

    def test_raise_at_a_question(self) -> None:
        """While a question waits, Ctrl+C raises KeyboardInterrupt and sets no flag."""
        interrupts = Interrupts()
        with self.assertRaises(KeyboardInterrupt), interrupts.question():
            interrupts.handle(signal.SIGINT, None)
        self.assertFalse(interrupts.requested)

    def test_main_installs_and_restores_the_handler(self) -> None:
        """`cli.main` runs with its handler installed and puts the previous one back."""
        seen: list[object] = []

        def handler(_context):
            """Record the SIGINT handler that is active while the command runs."""
            seen.append(signal.getsignal(signal.SIGINT))
            return CommandResult(value=None)
        previous = signal.getsignal(signal.SIGINT)
        with patch.object(runner, "load_handler", return_value=handler),                 patch.object(runner.observer_sessions, "open_observer_session", return_value=_Session()),                 patch.object(sys, "stdout", io.StringIO()), patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(cli_main(["server", "status"]), 0)
        self.assertEqual(getattr(seen[0], "__name__", ""), "handle")
        self.assertIs(signal.getsignal(signal.SIGINT), previous)


if __name__ == "__main__":
    unittest.main()
