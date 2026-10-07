"""Text and JSON rendering of one command result; the typed text parts of criterion 18 (design 6.3, 11.3)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO, Union

# Version of the JSON document; it changes only by adding fields (A11)
CLI_VERSION = 1


@dataclass(frozen=True)
class Label:
    """Operator prose or a catalogue text; it never holds an input name."""

    text: str


@dataclass(frozen=True)
class TypeText:
    """Text that the operator types: a command, an option or a choice value."""

    text: str


@dataclass(frozen=True)
class Echo:
    """A value that the operator typed, shown back as typed."""

    text: str


@dataclass(frozen=True)
class InputColumn:
    """A cell of a headed table column that holds an input name (for example a key or an ID)."""

    text: str


@dataclass(frozen=True)
class Value:
    """A value of the operator's own data, shown as stored: a name, a path, a time, a number or a log line."""

    text: str


# One piece of text with its position class
Part = Union[Label, TypeText, Echo, InputColumn, Value]
# Free text in a sentence is prose; the typed parts mark everything else
SentencePart = Union[str, Part]


@dataclass(frozen=True)
class Line:
    """One line of output, joined from its parts."""

    parts: tuple[Part, ...]


@dataclass(frozen=True)
class Table:
    """A table with a heading per column.

    `input_columns` names the columns that hold input names, `value_columns` the columns of stored data;
    every other cell is operator wording.
    """

    headings: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    input_columns: frozenset[int] = frozenset()
    value_columns: frozenset[int] = frozenset()

    def parts(self) -> tuple[Part, ...]:
        """Return the headings as labels and each cell typed by its column."""
        cells: list[Part] = [Label(heading) for heading in self.headings]
        for row in self.rows:
            cells.extend(InputColumn(cell) if index in self.input_columns
                         else Value(cell) if index in self.value_columns else Label(cell)
                         for index, cell in enumerate(row))
        return tuple(cells)


# One block of text output
Block = Union[Line, Table]


def sentence(*pieces: SentencePart) -> Line:
    """Build one line from prose strings and typed parts; adjacent prose is one label."""
    parts: list[Part] = []
    for piece in pieces:
        part = Label(piece) if isinstance(piece, str) else piece
        if isinstance(part, Label) and parts and isinstance(parts[-1], Label):
            parts[-1] = Label(parts[-1].text + part.text)
        elif part.text:
            parts.append(part)
    return Line(tuple(parts))


def line_text(line: Line) -> str:
    """Join the parts of one line into its text."""
    return "".join(part.text for part in line.parts)


def render_blocks(blocks: Iterable[Block]) -> str:
    """Join blocks into text: a line as it is, a table in aligned columns."""
    lines: list[str] = []
    for block in blocks:
        if isinstance(block, Line):
            lines.append(line_text(block))
            continue
        widths = [len(heading) for heading in block.headings]
        for row in block.rows:
            widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
        for row in (block.headings, *block.rows):
            lines.append("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    return "\n".join(lines)


def block_parts(blocks: Iterable[Block]) -> tuple[Part, ...]:
    """Return every typed part of the blocks, for the input-name test."""
    parts: list[Part] = []
    for block in blocks:
        parts.extend(block.parts if isinstance(block, Line) else block.parts())
    return tuple(parts)


@dataclass
class CommandResult:
    """What a command returns: its JSON value and its text blocks; `exit_code` 0 unless stated."""

    value: Any = None
    blocks: Sequence[Block] = ()
    exit_code: int = 0


@dataclass(eq=False)
class CliFailure(Exception):
    """A command ends without success: JSON code, operator text, exit code and optional details."""

    code: str
    message: Line
    exit_code: int
    retryable: bool = False
    details: Mapping[str, Any] | None = None
    notes: Sequence[Block] = field(default_factory=tuple)

    def __str__(self) -> str:
        """Return the operator text."""
        return line_text(self.message)


class Output:
    """Writes one command outcome: stdout holds only the result, stderr progress, notes and errors."""

    def __init__(self, stdout: TextIO, stderr: TextIO, *, json_mode: bool, command: str = "") -> None:
        """Keep the two streams, the mode and the command name of the JSON document."""
        self.stdout = stdout
        self.stderr = stderr
        self.json_mode = json_mode
        self.command = command
        # Input of a confirmation question (the runner sets it); None never asks
        self.stdin: TextIO | None = None
        # Every block written in text mode, for the tests of criterion 18
        self.written: list[Block] = []
        # Length of the progress line that a terminal shows now (rewritten with a carriage return)
        self._shown = 0

    def note(self, blocks: Iterable[Block]) -> None:
        """Write notes and progress to stderr, in both modes."""
        self.clear_progress()
        for block in blocks:
            self.written.append(block)
            self.stderr.write(render_blocks((block,)) + "\n")
        self.stderr.flush()

    def progress(self, line: Line, *, rewrite: bool, width: int = 80) -> None:
        """Write one progress line to stderr: rewritten in place on a terminal, else one line per change (7)."""
        self.written.append(line)
        text = line_text(line)
        if not rewrite:
            self.stderr.write(text + "\n")
        else:
            # Cut to the terminal width, so the line never wraps and the carriage return reaches its start
            text = text[:max(1, width - 1)]
            self.stderr.write("\r" + text.ljust(self._shown))
            self._shown = len(text)
        self.stderr.flush()

    def clear_progress(self) -> None:
        """Remove a rewritten progress line before other stderr text."""
        if self._shown:
            self.stderr.write("\r" + " " * self._shown + "\r")
            self.stderr.flush()
            self._shown = 0

    def show(self, blocks: Sequence[Block]) -> None:
        """Write result blocks to stdout at once, before a question (a review); JSON mode keeps them for the document."""
        if self.json_mode or not blocks:
            return
        self.written.extend(blocks)
        self.stdout.write(render_blocks(blocks) + "\n")
        self.stdout.flush()

    def success(self, result: CommandResult) -> int:
        """Write a successful result and return its exit code."""
        if self.json_mode:
            self._document({"success": True, "value": result.value})
        elif result.blocks:
            self.written.extend(result.blocks)
            self.stdout.write(render_blocks(result.blocks) + "\n")
            self.stdout.flush()
        return result.exit_code

    def failure(self, failure: CliFailure) -> int:
        """Write a failure (JSON document on stdout, or text on stderr) and return its exit code."""
        if failure.notes:
            self.note(failure.notes)
        if self.json_mode:
            error: dict[str, Any] = {"code": failure.code, "message": str(failure),
                                     "retryable": failure.retryable}
            if failure.details:
                error["details"] = dict(failure.details)
            self._document({"success": False, "error": error})
        else:
            self.note((failure.message,))
        return failure.exit_code

    def _document(self, body: Mapping[str, Any]) -> None:
        """Print the one JSON document of the run; ASCII escapes keep any console encoding safe."""
        document = {"cli_version": CLI_VERSION, "command": self.command, **body}
        self.stdout.write(json.dumps(document, ensure_ascii=True) + "\n")
        self.stdout.flush()
