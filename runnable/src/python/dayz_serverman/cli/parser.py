"""Command-line parsing and help from the registry; refusals raise UsageError instead of SystemExit(2) (6.2)."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any, NoReturn

from . import help_wording as texts
from .command_table import COMMANDS
from .registry import HELP_WORD, CommandSpec, Confirm, Form, OptionSpec, children, leaf_paths, longest_prefix

# The options that every command takes, in help order
COMMON_OPTIONS = (OptionSpec("json", Form.FLAG, "--json"), OptionSpec("yes", Form.FLAG, "--yes"),
                  OptionSpec("help", Form.FLAG, "--help"))
# A time of day for `schedule set`
TIME_PATTERN = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")


class UsageError(Exception):
    """The command line is not valid: exit 2, JSON code USAGE; `command` names the help to read."""

    def __init__(self, message: str, command: str | None = None) -> None:
        """Keep the parser message and the command whose help applies."""
        super().__init__(message)
        self.message = message
        self.command = command


class NoCommand(UsageError):
    """`--cli` with nothing after it: the command list goes to stderr, exit 2."""


@dataclass(frozen=True)
class HelpRequest:
    """A help page to print on stdout with exit 0."""

    text: str


@dataclass(frozen=True)
class ParsedCommand:
    """A leaf command with its parsed options."""

    spec: CommandSpec
    options: argparse.Namespace


class _Parser(argparse.ArgumentParser):
    """An argparse parser that raises UsageError instead of printing and exiting."""

    def error(self, message: str) -> NoReturn:
        """Turn a parser refusal into a UsageError for this command."""
        raise UsageError(message, self.prog)


def parse(arguments: list[str], commands: tuple[CommandSpec, ...] = COMMANDS) -> ParsedCommand | HelpRequest:
    """Return the leaf command and its options, or the help page that the command line asks for."""
    if not arguments:
        raise NoCommand("Name a command.")
    words = _leading_words(arguments)
    if words and words[0] == HELP_WORD:
        return _help_for(commands, words[1:])
    prefix = longest_prefix(commands, words)
    if "--help" in arguments:
        return _help_for(commands, list(prefix))
    leaf = leaf_paths(commands).get(prefix)
    if leaf is None:
        if prefix:
            raise UsageError(f"choose one of: {', '.join(_next_words(commands, prefix))}", " ".join(prefix))
        raise UsageError(f"unknown command {arguments[0]}")
    options = build_leaf_parser(leaf).parse_args(arguments[len(prefix):])
    return ParsedCommand(leaf, options)


def build_leaf_parser(spec: CommandSpec) -> argparse.ArgumentParser:
    """Build the argparse parser of one leaf command, without argparse's own help."""
    parser = _Parser(prog=spec.name, add_help=False, allow_abbrev=False)
    groups: dict[str, Any] = {}
    for option in (*spec.options, *COMMON_OPTIONS[:2]):
        target = parser
        if option.group is not None:
            target = groups.setdefault(option.group, parser.add_mutually_exclusive_group())
        _add_option(target, option)
    return parser


def _add_option(parser: Any, option: OptionSpec) -> None:
    """Add one option of the registry to an argparse parser or group."""
    if option.form is Form.POSITIONAL:
        parser.add_argument(option.dest, choices=option.choices or None, metavar=option.metavar)
    elif option.form is Form.TIME:
        parser.add_argument(option.dest, type=_time_of_day, metavar=option.metavar)
    elif option.form is Form.FLAG:
        parser.add_argument(option.flag, dest=option.dest, action="store_true")
    elif option.form is Form.BOOL_PAIR:
        parser.add_argument(option.flag, dest=option.dest, action=argparse.BooleanOptionalAction, default=None)
    elif option.form is Form.APPEND:
        parser.add_argument(option.flag, dest=option.dest, action="append", metavar=option.metavar)
    elif option.form is Form.INTEGER:
        parser.add_argument(option.flag, dest=option.dest, type=_integer(option), metavar=option.metavar,
                            required=option.required)
    elif option.form is Form.CHOICE:
        parser.add_argument(option.flag, dest=option.dest, choices=option.choices, required=option.required)
    else:
        parser.add_argument(option.flag, dest=option.dest, metavar=option.metavar, required=option.required)


def _integer(option: OptionSpec) -> Any:
    """Return an argparse type that reads a base-10 integer inside the option's range."""
    def convert(text: str) -> int:
        """Read the integer and check its range."""
        try:
            value = int(text, 10)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{text} is not a whole number") from None
        if (option.minimum is not None and value < option.minimum) or (
                option.maximum is not None and value > option.maximum):
            bounds = f"from {option.minimum} to {option.maximum}" if option.maximum is not None \
                else f"at least {option.minimum}"
            raise argparse.ArgumentTypeError(f"{text} is not {bounds}")
        return value
    return convert


def _time_of_day(text: str) -> str:
    """Accept a time of day from 00:00 to 23:59."""
    if TIME_PATTERN.fullmatch(text) is None:
        raise argparse.ArgumentTypeError(f"{text} is not a time from 00:00 to 23:59")
    return text


def _leading_words(arguments: list[str]) -> list[str]:
    """Return the words before the first option."""
    words: list[str] = []
    for argument in arguments:
        if argument.startswith("-"):
            break
        words.append(argument)
    return words


def _next_words(commands: tuple[CommandSpec, ...], prefix: tuple[str, ...]) -> list[str]:
    """Return the distinct next words under a group, in table order."""
    seen: list[str] = []
    for command in children(commands, prefix):
        word = command.path[len(prefix)] if len(command.path) > len(prefix) else None
        if word is not None and word not in seen:
            seen.append(word)
    return seen


def _help_for(commands: tuple[CommandSpec, ...], words: list[str]) -> HelpRequest:
    """Return the help of the general list, a group or a leaf command."""
    prefix = longest_prefix(commands, words)
    if len(prefix) != len(words):
        raise UsageError(f"unknown command {' '.join(words)}")
    leaf = leaf_paths(commands).get(prefix)
    if leaf is not None:
        return HelpRequest(leaf_help(leaf))
    return HelpRequest(command_list(commands, prefix))


def command_list(commands: tuple[CommandSpec, ...] = COMMANDS, prefix: tuple[str, ...] = ()) -> str:
    """Return the general help, or the commands of one group with their sentences."""
    # Commands grouped by noun in the order of the noun list, in table order inside a noun
    nouns = list(texts.NOUN_TEXTS)
    selected = sorted(children(commands, prefix),
                      key=lambda command: nouns.index(command.path[0]) if command.path[0] in nouns else len(nouns))
    width = max(len(command.name) for command in selected)
    lines = [texts.HELP_INTRO, texts.HELP_USAGE, "", texts.COMMAND_LIST_TITLE] if not prefix \
        else [texts.NOUN_TEXTS.get(prefix[0], ""), "", texts.COMMAND_LIST_TITLE]
    lines.extend(f"  {command.name.ljust(width)}  {texts.COMMAND_TEXTS[command.name]}" for command in selected)
    if not prefix:
        lines.extend(("", texts.EXIT_CODES_TITLE))
        lines.extend(f"  {code}  {text}" for code, text in texts.EXIT_CODE_TEXTS)
    lines.extend(("", texts.HELP_OUTRO))
    return "\n".join(lines)


def leaf_help(spec: CommandSpec) -> str:
    """Return the help page of one command: usage, sentence and options with their sentences.

    `--yes` is accepted everywhere (6.2) but listed only where a question exists (QF-44).
    """
    common = [option for option in COMMON_OPTIONS if option.dest != "yes" or spec.confirm is not Confirm.NONE]
    entries = [(_option_usage(option), texts.OPTION_TEXTS[option.dest]) for option in (*spec.options, *common)]
    width = max(len(usage) for usage, _text in entries)
    # Positional arguments first, then the options in table order
    ordered = sorted((*spec.options, *(option for option in common if option.dest != "help")),
                     key=lambda option: option.form not in (Form.POSITIONAL, Form.TIME))
    usage = " ".join(_option_usage(option, bracket=True) for option in ordered)
    lines = [f"Usage: DayZ-ServerMan.py --cli {spec.name} {usage}".rstrip(), "", texts.COMMAND_TEXTS[spec.name],
             "", texts.OPTION_LIST_TITLE]
    lines.extend(f"  {usage.ljust(width)}  {text}" for usage, text in entries)
    return "\n".join(lines)


def _option_usage(option: OptionSpec, *, bracket: bool = False) -> str:
    """Return how an option is written, for example `--target server|gameplay`."""
    if option.form in (Form.POSITIONAL, Form.TIME):
        return "|".join(option.choices) if option.choices else (option.metavar or option.dest.upper())
    if option.form is Form.BOOL_PAIR:
        written = f"{option.flag} | --no-{option.flag[2:]}"
    elif option.form is Form.FLAG:
        written = str(option.flag)
    elif option.form is Form.CHOICE:
        written = f"{option.flag} {'|'.join(option.choices)}"
    else:
        written = f"{option.flag} {option.metavar or option.dest.upper()}"
    return f"[{written}]" if bracket and not option.required else written
