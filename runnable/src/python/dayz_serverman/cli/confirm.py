"""Confirmation before a change (design 8.1): the review first, then the question, `--yes`, or a refusal.

The review is always computed before this point; `--yes` skips only the question. JSON mode
never asks, also on a terminal, and never reads stdin.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from ..adapters.windows.console import is_terminal
from .exit_codes import NOT_CONFIRMED
from .interrupts import Interrupts
from .output import Block, CliFailure, Output, sentence
from .wording import NOTHING_CHANGED, confirmation_needed, not_interactive

# The question, word for word as criterion 5 fixes it; its brackets show the answers, so it is not prose
QUESTION = "Continue? [y/N] "
# The answers that continue; any other answer, an empty line or the end of input declines
YES_ANSWERS = frozenset(("y", "yes"))


def confirm(
    output: Output, interrupts: Interrupts, stdin: TextIO | None, *, yes: bool,
    review: Sequence[Block], review_value: Any, nothing_changed: str = NOTHING_CHANGED,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Return when the change may go ahead; else raise exit 4 with the review in the JSON details.

    `nothing_changed` is the closing sentence of a refusal ("The mods are downloaded; nothing was
    applied." for `mods update`, criterion 24); `details` adds members to the error details.
    """
    refusal_details = {"review": review_value, **(details or {})}
    if output.json_mode:
        if yes:
            return
        raise CliFailure("CONFIRMATION_REQUIRED", confirmation_needed(nothing_changed), NOT_CONFIRMED,
                         details=refusal_details)
    # Text: the review goes to stdout before any question
    output.show(review)
    if yes:
        return
    if not is_terminal(stdin):
        # Never wait for input without a terminal
        raise CliFailure("CONFIRMATION_REQUIRED", confirmation_needed(nothing_changed), NOT_CONFIRMED,
                         details=refusal_details)
    assert stdin is not None
    output.stderr.write(QUESTION)
    output.stderr.flush()
    try:
        # Ctrl+C at the question raises here (6.7); nothing is being written now
        with interrupts.question():
            answer = stdin.readline()
    except (KeyboardInterrupt, EOFError):
        answer = ""
        output.stderr.write("\n")
    if answer.strip().casefold() in YES_ANSWERS:
        return
    raise CliFailure("CONFIRMATION_REQUIRED", sentence(nothing_changed), NOT_CONFIRMED, details=refusal_details)


def require_interactive(output: Output, stdin: TextIO | None) -> None:
    """Steam sign-in shares the console: refuse without a terminal or with JSON, whatever `--yes` says (8.1)."""
    if output.json_mode or not is_terminal(stdin):
        raise CliFailure("NOT_INTERACTIVE", not_interactive(), NOT_CONFIRMED)
