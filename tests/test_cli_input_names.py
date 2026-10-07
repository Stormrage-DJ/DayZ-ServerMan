"""Task 2.6, criterion 18 (11.3): input names appear only in a headed column after the label, in help, or as
text to type or an echo; operator prose holds no identifier."""

from __future__ import annotations

import argparse
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runnable" / "src" / "python"))

from session_fixtures import populate  # noqa: E402
from test_cli_read_commands import READS  # noqa: E402
from dayz_serverman.application.activity_wording import leaks_identifier  # noqa: E402
from dayz_serverman.application.field_wording import FIELD_LABELS  # noqa: E402
from dayz_serverman.cli import help_wording, runner, wording  # noqa: E402
from dayz_serverman.cli.command_table import COMMANDS  # noqa: E402
from dayz_serverman.cli.commands import backup, config, profile, server, status, tweaks  # noqa: E402
from dayz_serverman.cli.interrupts import Interrupts  # noqa: E402
from dayz_serverman.cli.output import (  # noqa: E402
    Echo, InputColumn, Label, Line, Output, Table, TypeText, Value, block_parts, sentence,
)
from dayz_serverman.cli.parser import COMMON_OPTIONS, command_list, leaf_help  # noqa: E402
from dayz_serverman.cli.registry import Form, longest_prefix  # noqa: E402

# Literal words that 11.3 allows as text to type on their own
TYPE_WORDS = frozenset(("--yes", "--overwrite", "--names"))
# An input name that cannot be an ordinary word: a flag, or a name with "_", "-", ".", a digit or an inner capital
IDENTIFIER_LIKE = re.compile(r"^--|[_.\-0-9]|[a-z][A-Z]")


def flags() -> set[str]:
    """Return every option flag of the parser, with the negative form of a pair."""
    found = {str(option.flag) for option in COMMON_OPTIONS}
    for command in COMMANDS:
        for option in command.options:
            if option.flag:
                found.add(option.flag)
                if option.form is Form.BOOL_PAIR:
                    found.add(f"--no-{option.flag[2:]}")
    return found


def valid_type_text(text: str) -> bool:
    """Report whether a text to type parses as a command line of the parser, or is an allowed literal."""
    if text in TYPE_WORDS:
        return True
    words = text.split()
    if words[0].startswith("--"):
        return words[0] in flags()
    if words[0] == "help":
        # `help <command>` names a command or a group of commands
        return len(longest_prefix(COMMANDS, words[1:])) == len(words) - 1
    prefix = longest_prefix(COMMANDS, words)
    if not prefix:
        return False
    options = [option for command in COMMANDS if command.path[:len(prefix)] == prefix for option in command.options]
    choices = {choice for option in options for choice in option.choices}
    known = {option.flag for option in options} | {"--json", "--yes"}
    rest = words[len(prefix):]
    # Each word is a known option, a choice value, or the value after an option
    return all(word in known if word.startswith("--") else word in choices or (index and rest[index - 1] in known)
               for index, word in enumerate(rest))


class InputNameChecker:
    """Applies the 11.3 rules to typed parts and tables."""

    def __init__(self, test: unittest.TestCase, input_names: set[str], typed: list[str]) -> None:
        """Keep the test, the identifier-like input names and the words of the command line."""
        self.test = test
        self.names = {name for name in input_names if IDENTIFIER_LIKE.search(name)}
        self.typed = typed

    def prose(self, text: str) -> None:
        """Prose holds no identifier and no input name."""
        self.test.assertFalse(leaks_identifier(text), text)
        for name in self.names:
            self.test.assertIsNone(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text), (name, text))

    def blocks(self, blocks: list) -> None:
        """Check every part of the blocks by its position class."""
        for block in blocks:
            if isinstance(block, Table):
                self.test.assertNotIn(0, block.input_columns, block.headings)
                self.test.assertTrue(all(index < len(block.headings) for index in block.input_columns))
            for part in (block.parts if isinstance(block, Line) else block.parts()):
                if isinstance(part, Label):
                    self.prose(part.text)
                elif isinstance(part, TypeText):
                    self.test.assertTrue(valid_type_text(part.text), part.text)
                elif isinstance(part, Echo):
                    self.test.assertIn(part.text, self.typed)
                elif isinstance(part, InputColumn):
                    self.test.assertIsInstance(block, Table)
                else:
                    self.test.assertIsInstance(part, Value)


class RecordingOutput(Output):
    """An Output that keeps every instance, so the test reads what each run wrote."""

    made: list["RecordingOutput"] = []

    def __init__(self, *args, **kwargs) -> None:
        """Create the output and remember it."""
        super().__init__(*args, **kwargs)
        RecordingOutput.made.append(self)


class InputNameTests(unittest.TestCase):
    """Every read command, every CLI sentence and every help page against the rules of 11.3."""

    @classmethod
    def setUpClass(cls) -> None:
        """Build the fixture and collect the input names: flags, keys, features, IDs and command names."""
        cls.temporary = tempfile.TemporaryDirectory(prefix="serverman_cli_names_")
        cls.manager, cls.dayz = populate(Path(cls.temporary.name))
        keys = {key for labels in FIELD_LABELS.values() for key in labels}
        backups = {path.stem for path in (cls.manager / "backups").glob("*.zip")}
        cls.input_names = flags() | keys | backups | {"livonia-main"} | {
            command.name for command in COMMANDS if " " in command.name}

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the fixture."""
        cls.temporary.cleanup()

    def test_every_read_command_in_text_mode(self) -> None:
        """Run each read on the fixture and check each written part."""
        for name, (arguments, _expected) in READS.items():
            RecordingOutput.made = []
            with self.subTest(command=name), patch.object(runner, "Output", RecordingOutput):
                runner.run(arguments, self.manager, stdout=io.StringIO(), stderr=io.StringIO(),
                           interrupts=Interrupts())
                written = [block for output in RecordingOutput.made for block in output.written]
                self.assertTrue(written)
                InputNameChecker(self, self.input_names, arguments).blocks(written)

    def test_answers_that_the_fixture_does_not_have(self) -> None:
        """Events, medical settings, gameplay values, named players, odd backups and unfinished work."""
        answers = {
            "load_mission_configuration": {"relative_path": "mpmissions\\x\\db\\events.xml", "adoption_required": True,
                                           "values": {"events": {"StaticHeliCrash": {"active": 1, "nominal": 3},
                                                                 "AnimalWolf": {"min": 1, "max": 4}}}},
            "load_medical_features": {"features": {"medical_loot_zones": {"enabled": True},
                                                   "medical_item_spawns": {"enabled": False}}},
            "load_configuration": {"relative_path": "cfgGameplay.json", "fields": [
                {"key": "GeneralData.disableBaseDamage", "kind": "boolean", "present": True, "value": True},
                {"key": "password", "kind": "string", "secret": True, "present": True, "value": "x"}]},
            "get_online_players": {"state": "OK", "players": [{"name": "Somebody", "duration_seconds": 4000},
                                                               {"name": "", "duration_seconds": 10}]},
            "list_backups": {"destination_kind": "custom", "diagnostics": [{"message": "A backup has an invalid manifest."}],
                             "backups": [{"backup_id": "livonia-main_2026", "created_at": "bad",
                                          "restore_compatibility": "LEGACY_PROFILE_SCHEMA", "total_size": 9}]},
            "get_application_snapshot": {}, "get_ui_preferences": {"automatic_update_checks": False},
            "get_server_status": {"state": "RUNNING_MANAGED", "readiness": "READY", "players": 2, "max_players": 60},
            "get_update_status": {"mods": {"check_state": "FAILED", "error_code": "TIMEOUT", "update_count": 1},
                                  "server_build": {"state": "UNKNOWN_INSTALLATION", "reason": "NO_DAYZ_FOLDER"}},
            "list_profiles": [],
        }
        runs = (
            (tweaks.tweaks_show, {"target": "events"}), (tweaks.medical_show, {}),
            (config.config_show, {"target": "gameplay"}), (server.players, {"names": True}),
            (server.players, {"names": False}), (backup.backup_list, {"all": False}), (status.run, {}),
            (profile.profile_list, {}),
        )
        for handler, options in runs:
            output = Output(io.StringIO(), io.StringIO(), json_mode=False)
            context = SimpleNamespace(call=lambda method, **_parameters: answers[method], output=output,
                                      options=argparse.Namespace(**options), profile={"display_name": "Alpha"},
                                      session=SimpleNamespace(paths=None))
            with self.subTest(handler=handler.__name__, options=options), \
                    patch.object(status, "pending_recoveries", return_value=("RESTORE_BACKUP", "IMPORT_LEGACY")):
                output.success(handler(context))
                InputNameChecker(self, self.input_names | {"medical_loot_zones", "GeneralData.disableBaseDamage"},
                                 []).blocks(list(output.written))

    def test_cli_sentences(self) -> None:
        """Every sentence builder of `cli/wording.py` and every way out of the catalogue follows the rules."""
        lines = [wording.instance_active(None), wording.no_profile_named("Nobody"), wording.no_profile_exists(),
                 wording.name_the_profile("Alpha"), wording.unknown_backup("x-1"), wording.unknown_key("maxPlayrs",
                 "config show --target server"), wording.duplicate_key("hostname"), wording.invalid_set("x"),
                 wording.invalid_file("f.json"), wording.overwrite_needs_replace(),
                 wording.profile_ambiguous("Alpha", ["alpha", "alpha-2"]), wording.usage("bad", "server start")]
        lines += [wording.way_out(needle) for needle, _replacement in wording.WAY_OUT]
        typed = ["Nobody", "x-1", "maxPlayrs", "hostname", "x", "f.json", "Alpha", "bad"]
        checker = InputNameChecker(self, self.input_names - {"alpha", "alpha-2"}, typed)
        for line in lines:
            with self.subTest(line=line):
                # The ID list of an ambiguous name is text to type; it is not prose
                checker.blocks([Line(tuple(part for part in line.parts
                                           if not (isinstance(part, TypeText) and part.text.startswith("alpha"))))])

    def test_the_checker_refuses_misplaced_names(self) -> None:
        """A flag or an ID in prose, a bad text to type, a foreign echo, an ID column first, a raw code: refused."""
        checker = InputNameChecker(unittest.TestCase(), self.input_names, [])
        cases = ([sentence("Run it with --profile now.")], [sentence("Use livonia-main.")],
                 [sentence(TypeText("nonsense words"))], [sentence(Echo("typed"))],
                 [Table(("ID", "Profile"), (("x", "y"),), input_columns=frozenset({0}))],
                 [sentence("Code REVISION_CONFLICT.")])
        for blocks in cases:
            with self.subTest(blocks=blocks), self.assertRaises(AssertionError):
                checker.blocks(blocks)

    def test_help_pages(self) -> None:
        """Help may show input names; each sentence of a command, noun or option holds no identifier."""
        texts = [*help_wording.COMMAND_TEXTS.values(), *help_wording.NOUN_TEXTS.values(),
                 *help_wording.OPTION_TEXTS.values(), help_wording.HELP_INTRO, help_wording.HELP_OUTRO]
        texts += [text for _code, text in help_wording.EXIT_CODE_TEXTS]
        for text in texts:
            with self.subTest(text=text):
                self.assertFalse(leaks_identifier(text))
        self.assertIn("--names", leaf_help(next(command for command in COMMANDS if command.name == "server players")))
        self.assertIn("status", command_list())
        self.assertEqual(block_parts([]), ())


if __name__ == "__main__":
    unittest.main()
