"""Task 3.2, criteria 5 and 6 (design 8.1, 8.2): the review first, then the question, --yes or a refusal."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.application import review_wording  # noqa: E402
from dayz_serverman.cli import wording  # noqa: E402
from dayz_serverman.cli.confirm import QUESTION, confirm, require_interactive  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CliFailure, Output, line_text  # noqa: E402
from dayz_serverman.cli.review import lifecycle_review  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "runnable" / "src" / "frontend"
PROFILE = {"profile_id": "livonia-main", "display_name": "Livonia Main"}


class FakeTerminal(io.StringIO):
    """Stdin of a terminal (or not) that records whether it was read; an answer of None raises Ctrl+C."""

    def __init__(self, answer: str | None = "", *, terminal: bool = True) -> None:
        """Keep the answer and the terminal flag."""
        super().__init__()
        self.answer = answer
        self.terminal = terminal
        self.reads = 0

    def isatty(self) -> bool:
        """Report the terminal flag."""
        return self.terminal

    def readline(self, *_args: object) -> str:
        """Return the answer line, or raise KeyboardInterrupt as Ctrl+C at the question does."""
        self.reads += 1
        if self.answer is None:
            raise KeyboardInterrupt
        return self.answer


class ConfirmTests(unittest.TestCase):
    """Each row of the 8.1 table."""

    def ask(self, *, json_mode: bool = False, yes: bool = False, stdin: FakeTerminal | None = None,
            nothing_changed: str = wording.NOTHING_CHANGED) -> tuple[int | None, Output, FakeTerminal]:
        """Run the confirmation of a stop review; return the exit code (None when it may go ahead)."""
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        output = Output(self.stdout, self.stderr, json_mode=json_mode)
        stdin = stdin if stdin is not None else FakeTerminal("y\n")
        blocks, value = lifecycle_review("stop", PROFILE, True)
        try:
            confirm(output, Interrupts(), stdin, yes=yes, review=blocks, review_value=value,
                    nothing_changed=nothing_changed)
        except CliFailure as failure:
            output.failure(failure)
            return failure.exit_code, output, stdin
        return None, output, stdin

    def test_text_on_a_terminal_asks_after_the_review(self) -> None:
        """Only y or yes (any case, trimmed) continues; the review is on stdout and the question on stderr."""
        for answer in ("y\n", "YES\n", "  Yes  \n"):
            with self.subTest(answer=answer):
                code, _output, stdin = self.ask(stdin=FakeTerminal(answer))
                self.assertIsNone(code)
                self.assertEqual(stdin.reads, 1)
                self.assertIn("Save and stop DayZ?", self.stdout.getvalue())
                self.assertIn("Backup after stop: yes", self.stdout.getvalue())
                self.assertEqual(self.stderr.getvalue(), QUESTION)

    def test_any_other_answer_end_of_input_or_ctrl_c_exits_4(self) -> None:
        """No, an empty line, end of input and Ctrl+C at the question change nothing: exit 4."""
        for answer in ("n\n", "\n", "", "yep\n", None):
            with self.subTest(answer=answer):
                code, _output, _stdin = self.ask(stdin=FakeTerminal(answer))
                self.assertEqual(code, 4)
                self.assertTrue(self.stderr.getvalue().rstrip().endswith(wording.NOTHING_CHANGED))

    def test_text_without_a_terminal_refuses_without_reading(self) -> None:
        """The review, then the sentence that names --yes; stdin is never read (criterion 6)."""
        code, _output, stdin = self.ask(stdin=FakeTerminal("y\n", terminal=False))
        self.assertEqual((code, stdin.reads), (4, 0))
        self.assertIn("Save and stop DayZ?", self.stdout.getvalue())
        self.assertIn("Confirmation is needed. Nothing was changed. Run the command again with --yes.",
                      self.stderr.getvalue())

    def test_yes_prints_the_review_and_never_asks(self) -> None:
        """--yes skips only the question: the review is still printed."""
        code, _output, stdin = self.ask(yes=True)
        self.assertEqual((code, stdin.reads), (None, 0))
        self.assertIn("Save and stop DayZ?", self.stdout.getvalue())

    def test_json_never_asks_also_on_a_terminal(self) -> None:
        """--json without --yes: exit 4, CONFIRMATION_REQUIRED, the review in the details; stdin never read."""
        code, _output, stdin = self.ask(json_mode=True, stdin=FakeTerminal("y\n"))
        self.assertEqual((code, stdin.reads), (4, 0))
        document = json.loads(self.stdout.getvalue())
        self.assertEqual(document["error"]["code"], "CONFIRMATION_REQUIRED")
        self.assertIn("--yes", document["error"]["message"])
        self.assertEqual(document["error"]["details"]["review"],
                         {"action": "stop", "profile_id": "livonia-main", "backup_after_stop": True})
        self.assertEqual(self.stderr.getvalue(), "")

    def test_json_with_yes_continues_without_output(self) -> None:
        """--json --yes: nothing is printed before the one document of the command."""
        code, _output, stdin = self.ask(json_mode=True, yes=True)
        self.assertEqual((code, stdin.reads, self.stdout.getvalue()), (None, 0, ""))

    def test_mods_update_refusal_ends_with_the_download_sentence(self) -> None:
        """Criterion 24: the refusal's last line names the finished download, not "Nothing was changed."."""
        for stdin in (FakeTerminal("n\n"), FakeTerminal("y\n", terminal=False)):
            with self.subTest(terminal=stdin.terminal):
                code, _output, _stdin = self.ask(stdin=stdin, nothing_changed=wording.MODS_DOWNLOADED)
                self.assertEqual(code, 4)
                self.assertIn(wording.MODS_DOWNLOADED, self.stderr.getvalue().splitlines()[-1])
                self.assertNotIn(wording.NOTHING_CHANGED, self.stderr.getvalue())

    def test_steam_login_needs_a_terminal_and_text(self) -> None:
        """NOT_INTERACTIVE exit 4 without a terminal or with --json, whatever --yes says."""
        for json_mode, terminal in ((False, False), (True, True), (True, False)):
            with self.subTest(json_mode=json_mode, terminal=terminal):
                output = Output(io.StringIO(), io.StringIO(), json_mode=json_mode)
                with self.assertRaises(CliFailure) as raised:
                    require_interactive(output, FakeTerminal(terminal=terminal))
                self.assertEqual((raised.exception.exit_code, raised.exception.code), (4, "NOT_INTERACTIVE"))
        require_interactive(Output(io.StringIO(), io.StringIO(), json_mode=False), FakeTerminal())


class LifecycleReviewTests(unittest.TestCase):
    """8.2: the lifecycle reviews are the Overview dialogs' texts; JSON gets the confirmed request."""

    def test_each_review_text_is_a_literal_of_the_dialog(self) -> None:
        """Every title, body and the backup sentence appear unchanged in overview_lifecycle_dialog.js."""
        source = (FRONTEND / "overview_lifecycle_dialog.js").read_text(encoding="utf-8")
        texts = [text for pair in review_wording.LIFECYCLE_DIALOGS.values() for text in pair]
        for text in texts:
            with self.subTest(text=text):
                self.assertIn(f'"{text}"', source)
        # The backup sentence follows the body inside a template literal
        self.assertIn("${baseMessage} " + review_wording.BACKUP_AFTER_STOP_SENTENCE + "`", source)

    def test_review_lines(self) -> None:
        """Start names the profile; stop and restart add the backup choice, and the backup sentence when on."""
        blocks, value = lifecycle_review("start", PROFILE)
        self.assertEqual([line_text(block) for block in blocks],
                         ["Start DayZ server?", "Start the selected profile now.", "Profile: Livonia Main"])
        self.assertEqual(value, {"action": "start", "profile_id": "livonia-main"})
        blocks, value = lifecycle_review("restart", PROFILE, False)
        self.assertEqual([line_text(block) for block in blocks][1:], [
            "Save and stop the managed process, then start the selected profile.", "Profile: Livonia Main",
            "Backup after stop: no"])
        blocks, _value = lifecycle_review("stop", PROFILE, True)
        self.assertEqual(line_text(blocks[1]), "Request a graceful save and wait for DayZ to close. "
                                               "A verified backup will be created after DayZ stops.")


class NewSentenceTests(unittest.TestCase):
    """Criterion 18 for the sentences of 3.2: input names only as text to type or an echo."""

    def test_sentences_follow_the_input_name_rules(self) -> None:
        """The waiter notes, the confirmation and refusal sentences, the reviews and the missing-data sentences."""
        from test_cli_input_names import InputNameChecker, flags
        from dayz_serverman.cli.output import sentence
        from dayz_serverman.cli.waiter import end_line
        lines = [sentence(text) for text in (
            wording.CANCEL_REQUESTED, wording.NOT_CANCELLABLE, wording.ALREADY_CANCELLING, wording.SIGN_IN_PROMPT,
            wording.READ_CANCELLED, wording.STEP_CANCELLED, wording.NOTHING_CHANGED, wording.MODS_DOWNLOADED)]
        lines += [wording.confirmation_needed(), wording.confirmation_needed(wording.MODS_DOWNLOADED),
                  wording.not_interactive(), wording.missing_configuration("gameplay", "Livonia Main"),
                  wording.missing_mission("Livonia Main"), wording.unknown_feature("noSuchFeature")]
        for action in ("start", "stop", "restart"):
            lines += lifecycle_review(action, PROFILE, None if action == "start" else True)[0]
        for state in ("SUCCEEDED", "CANCELLED", "FAILED", "RECOVERY_REQUIRED"):
            lines.append(end_line({"state": state, "terminal_error": {"code": "EXTERNAL_PROCESS", "message": "x"}},
                                  "RESTART_SERVER"))
        checker = InputNameChecker(self, flags() | {"livonia-main"}, ["noSuchFeature"])
        for line in lines:
            with self.subTest(line=line_text(line)):
                checker.blocks([line])


if __name__ == "__main__":
    unittest.main()
