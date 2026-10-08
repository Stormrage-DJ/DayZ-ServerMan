"""Shared steps of writing commands: the revisions that a write sends and the recovery gate before a question.

Both run before any review question (rule 5, criterion 28): a pinned revision that differs
exits 3, and a recovery block that the snapshot already shows exits 6, so a script never gets
exit 4 for a change that the state refuses anyway.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ...application.activity_wording import conflict_text
from ..confirm import confirm
from ..exit_codes import RECOVERY, REFUSED
from ..flow import stop_if_interrupted
from ..output import Block, CliFailure, CommandResult
from ..wording import revision_pinned, way_out
from .common import profile_id


class WriteState(NamedTuple):
    """What a write read before its question: the profile record, both revisions and the snapshot."""

    profile: Mapping[str, Any]
    profile_revision: Any
    settings_revision: Any
    snapshot: Mapping[str, Any]


def read_revisions(context: Any) -> WriteState:
    """Read the profile and settings revisions that the write sends; `--expect-…` pins them (10 rule).

    The profile record and the application snapshot come with them, so the caller can gate on them.
    """
    stop_if_interrupted(context.interrupts)
    record = context.call("read_profile", profile_id=profile_id(context.profile))
    profile_revision = record.get("revision")
    snapshot = context.call("get_application_snapshot")
    settings_revision = snapshot.get("settings", {}).get("revision")
    check_pins(context, profile_revision, settings_revision)
    return WriteState(record, profile_revision, settings_revision, snapshot)


def check_pins(context: Any, profile_revision: Any, settings_revision: Any) -> None:
    """Exit 3 when an `--expect-…` pin differs from the revision just read (QF-45).

    The text names the revision that differs and how to read it again: the same pin never
    succeeds, so "run the command again" would mislead a script.
    """
    pins = (("profile", getattr(context.options, "expect_profile_revision", None), profile_revision),
            ("settings", getattr(context.options, "expect_settings_revision", None), settings_revision))
    for subject, pinned, read in pins:
        if pinned is not None and pinned != read:
            raise CliFailure("REVISION_CONFLICT", revision_pinned(subject, read, pinned), REFUSED, True,
                             {"revision": subject, "expected": pinned, "current": read})


def check_recovery_block(snapshot: Mapping[str, Any]) -> None:
    """Refuse with exit 6 when the snapshot shows a recovery block; the window's reason with the CLI way out."""
    block = snapshot.get("mutation_block")
    if block:
        text = conflict_text(block, "RECOVERY_BLOCK", snapshot.get("mutation_block_owner"))
        raise CliFailure("RECOVERY_REQUIRED", way_out(text), RECOVERY)


def ask(context: Any, blocks: Sequence[Block], review: Any) -> None:
    """Ctrl+C during the reads exits 5; then the 8.1 question, `--yes` or a refusal with exit 4."""
    stop_if_interrupted(context.interrupts)
    confirm(context.output, context.interrupts, context.stdin, yes=context.options.yes, review=blocks,
            review_value=review)


def write_result(record: Mapping[str, Any], review: Any, blocks: Sequence[Block]) -> CommandResult:
    """Return the value of a write with one lane operation (6.3) and its text blocks."""
    return CommandResult({"operations": [dict(record)], "review": review, "result": record.get("result")},
                         list(blocks))
