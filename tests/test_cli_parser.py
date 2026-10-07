"""Task 2.5, criterion 9: the parser takes every command and option of the registry; usage errors and help."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from dayz_serverman.cli import help_wording  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli import presteps  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import CliFailure  # noqa: E402
from dayz_serverman.cli.parser import HelpRequest, ParsedCommand, UsageError, parse  # noqa: E402
from dayz_serverman.cli.registry import Form, OptionSpec  # noqa: E402
from dayz_serverman.cli.runner import run  # noqa: E402


def example(option: OptionSpec) -> tuple[list[str], object]:
    """Return command-line words for one option and the value the parser must give."""
    if option.form is Form.POSITIONAL:
        value = option.choices[-1] if option.choices else "item-1"
        return [value], value
    if option.form is Form.TIME:
        return ["04:30"], "04:30"
    if option.form is Form.FLAG:
        return [str(option.flag)], True
    if option.form is Form.BOOL_PAIR:
        return [f"--no-{str(option.flag)[2:]}"], False
    if option.form is Form.APPEND:
        return [str(option.flag), "first=1", str(option.flag), "second=2"], ["first=1", "second=2"]
    if option.form is Form.INTEGER:
        value = option.maximum if option.maximum is not None else 7
        return [str(option.flag), str(value)], value
    if option.form is Form.CHOICE:
        return [str(option.flag), option.choices[-1]], option.choices[-1]
    return [str(option.flag), "C:\\Some Folder"], "C:\\Some Folder"


def command_line(options: tuple[OptionSpec, ...], target: OptionSpec | None = None) -> list[str]:
    """Return the words of the positional and required options, plus `target`, in registry order."""
    words: list[str] = []
    for option in options:
        if option is target or option.required or option.form in (Form.POSITIONAL, Form.TIME):
            words.extend(example(option)[0])
    return words


def run_cli(arguments: list[str]) -> tuple[int, str, str]:
    """Run the CLI on string streams and return the exit code, stdout and stderr."""
    stdout, stderr = io.StringIO(), io.StringIO()
    code = run(arguments, None, stdout=stdout, stderr=stderr, interrupts=Interrupts())
    return code, stdout.getvalue(), stderr.getvalue()


class ParserTableTests(unittest.TestCase):
    """Every command and every option of the registry parses to its value (table-driven)."""

    def test_every_option_of_every_command(self) -> None:
        """Each option, given alone with the required ones, reaches the namespace with its value."""
        cases = 0
        for command in COMMANDS:
            for option in command.options:
                _words, value = example(option)
                line = [*command.path, *command_line(command.options, option)]
                with self.subTest(command=command.name, option=option.dest):
                    parsed = parse(line)
                    self.assertIsInstance(parsed, ParsedCommand)
                    self.assertIs(parsed.spec, command)
                    self.assertEqual(getattr(parsed.options, option.dest), value)
                    cases += 1
        self.assertGreater(cases, 80)

    def test_common_options_on_every_command(self) -> None:
        """`--json` and `--yes` are taken by every command; absent options keep their defaults."""
        for command in COMMANDS:
            with self.subTest(command=command.name):
                parsed = parse([*command.path, *command_line(command.options), "--json", "--yes"])
                self.assertTrue(parsed.options.json and parsed.options.yes)
                plain = parse([*command.path, *command_line(command.options)])
                self.assertFalse(plain.options.json or plain.options.yes)

    def test_refusals_raise_usage_errors(self) -> None:
        """Unknown commands and options, missing or bad values and exclusive pairs are usage errors."""
        lines = (
            ["unknown"], ["server"], ["server", "launch"], ["status", "--unknown"], ["config", "show"],
            ["config", "show", "--target", "mission"], ["logs", "--lines", "0"], ["logs", "--lines", "1001"],
            ["logs", "--lines", "ten"], ["schedule", "set", "24:00", "stop"], ["schedule", "set", "04:30", "pause"],
            ["mods", "update", "--start", "--restart"], ["settings", "set", "--backup-root", "X",
                                                         "--default-backup-root"],
            ["migrate", "apply", "C:\\Old", "--item", "a", "--all-items"], ["server", "start", "--ready-timeout", "9"],
            ["help", "server", "launch"], ["profile", "restore"], ["steam", "set"], ["backup", "restore"],
        )
        for line in lines:
            with self.subTest(line=line):
                with self.assertRaises(UsageError):
                    parse(line)

    def test_help_requests(self) -> None:
        """`help`, `help <noun>`, `help <command>` and `<command> --help` return help pages."""
        for line in (["help"], ["help", "server"], ["help", "tweaks", "medical", "set"], ["server", "start", "--help"],
                     ["--help"], ["backup", "--help"]):
            with self.subTest(line=line):
                self.assertIsInstance(parse(line), HelpRequest)


class UsageExitTests(unittest.TestCase):
    """Usage errors exit 2 in text and JSON; help exits 0 on stdout; no arguments list the commands on stderr."""

    def test_usage_error_in_text_goes_to_stderr(self) -> None:
        """A text usage error prints nothing on stdout and one sentence with the help command on stderr."""
        code, stdout, stderr = run_cli(["logs", "--lines", "0"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("Run help logs for the commands and options.", stderr)

    def test_usage_error_in_json_is_one_document(self) -> None:
        """With `--json` anywhere, a usage error is one document with code USAGE on stdout."""
        for line in (["logs", "--json", "--lines", "0"], ["--json", "unknown"], ["server", "--json"]):
            with self.subTest(line=line):
                code, stdout, _stderr = run_cli(line)
                document = json.loads(stdout)
                self.assertEqual(code, 2)
                self.assertEqual(document["cli_version"], 1)
                self.assertFalse(document["success"])
                self.assertEqual(document["error"]["code"], "USAGE")

    def test_help_goes_to_stdout_with_exit_0(self) -> None:
        """Help pages print on stdout; each command and option of the page has its sentence."""
        code, stdout, stderr = run_cli(["help"])
        self.assertEqual((code, stderr), (0, ""))
        for command in COMMANDS:
            self.assertIn(help_wording.COMMAND_TEXTS[command.name], stdout)
        code, stdout, stderr = run_cli(["tweaks", "medical", "set", "--help"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Usage: DayZ-ServerMan.py --cli tweaks medical set FEATURE on|off [--profile ID-OR-NAME]", stdout)
        self.assertIn(help_wording.OPTION_TEXTS["feature"], stdout)

    def test_no_command_lists_the_commands_on_stderr(self) -> None:
        """`--cli` alone prints the command list to stderr and exits 2."""
        code, stdout, stderr = run_cli([])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn(help_wording.COMMAND_LIST_TITLE, stderr)

    def test_every_command_and_option_has_help_text(self) -> None:
        """The help catalogue has a sentence for each command, noun and option of the registry."""
        for command in COMMANDS:
            self.assertIn(command.name, help_wording.COMMAND_TEXTS)
            self.assertIn(command.path[0], help_wording.NOUN_TEXTS)
            for option in command.options:
                self.assertIn(option.dest, help_wording.OPTION_TEXTS)
        self.assertEqual(set(help_wording.COMMAND_TEXTS), {command.name for command in COMMANDS})



class EditInputTests(unittest.TestCase):
    """6.4.1 and 10.6: `--from-file` must be a JSON object and each `--set` a known key once, as key=value."""

    def test_from_file_shapes(self) -> None:
        """An object is read; a list, invalid JSON and a missing file exit 2."""
        with tempfile.TemporaryDirectory(prefix="serverman_from_file_") as temporary:
            root = Path(temporary)
            (root / "object.json").write_text('{"maxPlayers": 10}', encoding="utf-8")
            (root / "list.json").write_text("[1]", encoding="utf-8")
            (root / "broken.json").write_text("{", encoding="utf-8")
            self.assertEqual(presteps._from_file(str(root / "object.json")), {"maxPlayers": 10})
            for name in ("list.json", "broken.json", "missing.json"):
                with self.subTest(name=name), self.assertRaises(CliFailure) as refused:
                    presteps._from_file(str(root / name))
                self.assertEqual(refused.exception.exit_code, 2)

    def test_set_pairs(self) -> None:
        """The value may hold "="; an unknown key, a key twice and a missing "=" exit 2."""
        self.assertEqual(presteps._checked_sets(["a=x=y", "b="], {"a", "b"}, "config show"),
                         [("a", "x=y"), ("b", "")])
        for values in (["c=1"], ["a=1", "a=2"], ["a"], ["=1"]):
            with self.subTest(values=values), self.assertRaises(CliFailure) as refused:
                presteps._checked_sets(values, {"a", "b"}, "config show")
            self.assertEqual(refused.exception.exit_code, 2)


if __name__ == "__main__":
    unittest.main()
