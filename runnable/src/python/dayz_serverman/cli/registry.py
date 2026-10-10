"""Command specifications and lookups of the CLI registry (design 6.2); the table is in `command_table.py`."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class Kind(str, Enum):
    """READ runs in an observer session; WRITE runs in an owner session behind the instance lock."""

    READ = "READ"
    WRITE = "WRITE"


class Confirm(str, Enum):
    """Whether the command asks before it changes something, and whether a review comes first."""

    NONE = "NONE"
    CONFIRM = "CONFIRM"
    REVIEW = "REVIEW"


class ProfileRule(str, Enum):
    """How the command finds its profile (P4, design 6.6)."""

    NONE = "NONE"
    READ_DEFAULT = "READ_DEFAULT"
    WRITE_REQUIRED = "WRITE_REQUIRED"


class Form(str, Enum):
    """How an option is written on the command line."""

    FLAG = "FLAG"              # --names
    VALUE = "VALUE"            # --account NAME
    INTEGER = "INTEGER"        # --lines 50, with an optional range
    CHOICE = "CHOICE"          # --target server
    APPEND = "APPEND"          # --set k=v, repeatable
    BOOL_PAIR = "BOOL_PAIR"    # --backup-after-stop / --no-backup-after-stop
    POSITIONAL = "POSITIONAL"  # backup restore ID
    TIME = "TIME"              # schedule set 04:30


@dataclass(frozen=True)
class OptionSpec:
    """One option or positional argument of a command."""

    dest: str
    form: Form
    flag: str | None = None
    choices: tuple[str, ...] = ()
    required: bool = False
    group: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    metavar: str | None = None


@dataclass(frozen=True)
class CommandSpec:
    """One leaf command: path, session kind, questions, profile rule, options and bridge methods.

    `handler` and `prestep` name a function as "module:function" under `dayz_serverman.cli`.
    A command without a handler is PENDING: its phase of the plan has not built it yet.
    """

    path: tuple[str, ...]
    kind: Kind
    phase: int
    bridge_methods: frozenset[str]
    options: tuple[OptionSpec, ...] = ()
    confirm: Confirm = Confirm.NONE
    profile: ProfileRule = ProfileRule.NONE
    handler: str | None = None
    prestep: str | None = None

    @property
    def name(self) -> str:
        """Return the command name of the JSON document: the path joined with spaces."""
        return " ".join(self.path)

    @property
    def pending(self) -> bool:
        """Report whether a later phase still has to build this command."""
        return self.handler is None


# Bridge methods that only the CLI machinery calls: the waiter, Ctrl+C and the session close (01.04)
INTERNAL_METHODS = frozenset(("get_operation", "read_operation_events", "request_operation_cancellation",
                              "request_shutdown"))
# Reason of the two legacy backup reference methods (01.04)
_NOT_PARITY = "Never called by the GUI; no operator documentation; new feature, not parity"
# Bridge methods that no command calls, each with the Product Owner's reason in 01.04 of the CLI plan
EXCLUSION_REASONS = MappingProxyType({
    "save_selected_profile": "GUI selection memory; a script must not move the window's selection",
    "list_legacy_backup_references": _NOT_PARITY,
    "revalidate_legacy_backup_references": _NOT_PARITY,
})
EXCLUDED_METHODS = frozenset(EXCLUSION_REASONS)
# Start of each PENDING_TASK_10 value
PENDING_PREFIX = "pending task 10: "
# Bridge methods whose command task 10 of the mission map plan builds, each with
# "pending task 10: <planned command>". A pending method is not an exclusion. Task 10 empties this table.
PENDING_TASK_10: MappingProxyType[str, str] = MappingProxyType({})
# Command words that are not leaf commands of the table
HELP_WORD = "help"


def leaf_paths(commands: tuple[CommandSpec, ...]) -> dict[tuple[str, ...], CommandSpec]:
    """Return the commands keyed by their path."""
    return {command.path: command for command in commands}


def children(commands: tuple[CommandSpec, ...], prefix: tuple[str, ...]) -> list[CommandSpec]:
    """Return the commands under a path prefix, in table order."""
    return [command for command in commands if command.path[:len(prefix)] == prefix]


def longest_prefix(commands: tuple[CommandSpec, ...], words: list[str]) -> tuple[str, ...]:
    """Return the longest start of `words` that is a command or a group of commands."""
    best: tuple[str, ...] = ()
    for length in range(1, len(words) + 1):
        candidate = tuple(words[:length])
        if children(commands, candidate):
            best = candidate
        else:
            break
    return best
