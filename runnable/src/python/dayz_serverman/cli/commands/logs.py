"""`logs [--source manager/diagnostics/server] [--lines N]` (10.1)."""

from __future__ import annotations

from typing import Any

from ..output import CommandResult, Line, Value, sentence

# Bridge source per `--source` choice, and the default number of lines
SOURCES = {"manager": "manager", "diagnostics": "manager_diagnostics", "server": "server"}
DEFAULT_LINES = 100


def logs(context: Any) -> CommandResult:
    """Print the latest lines of one log as they are stored; Manager activity lines are already worded."""
    answer = context.call("read_log", source=SOURCES[context.options.source or "manager"],
                          maximum_lines=context.options.lines or DEFAULT_LINES)
    lines: list[Line] = [Line((Value(str(text)),)) for text in answer.get("lines", [])]
    if not lines:
        lines = [sentence("The log has no lines yet.")]
    elif answer.get("truncated"):
        lines.insert(0, sentence("Older lines are not shown."))
    return CommandResult(answer, lines)
