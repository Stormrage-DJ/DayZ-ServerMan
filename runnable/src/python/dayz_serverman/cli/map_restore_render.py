"""Review blocks of the restore answer for `map restore` (leaf 10.32, design D12 and D14).

The sections, their content and their order are those of the restore preview of the window
(`map_editor_preview_restore.js`, 8.10). A section without content is not printed.

The 8.8 texts of the answer print unchanged: the refusals, the 6.11 family text, the shared-original text, the
kept-changes sentence and the backup lines. Lists print every row; the window collapses long lists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import map_restore_wording as words
from .commands.common import local_time, profile_line
from .map_publish_wording import DELETED_PROFILE
from .map_review_render import INDENT, ROW
from .output import Block, Value, sentence


def restore_blocks(profile: Mapping[str, Any] | None, mode: str, answer: Mapping[str, Any]) -> list[Block]:
    """Return the review of one restore answer: the profile, the five sections, then the stop-server line."""
    original = _mapping(answer.get("original")) if mode == "original" else {}
    external = _items(answer.get("external_changes"))
    sections = (
        (words.REFUSALS_HEADING, _refusals(answer)),
        (words.NOTICES_HEADING, _notices(mode, answer, original)),
        (words.WRITES_HEADING, [*_text(writes_text(mode, answer)),
                                *_list(words.AFFECTED, [item.get("path") for item in
                                                        _items(answer.get("affected_files"))])]),
        # The backup lines are never collapsed in the window
        (words.OVERWRITTEN_HEADING, [*(line for item in external if item.get("overwritten")
                                       for line in _text(item.get("text"))),
                                     *_list(words.COORDINATED, [item.get("path") for item in
                                                                _items(answer.get("coordinated_changes"))
                                                                if item.get("overwritten")])]),
        (words.KEPT_HEADING, _list(None, [words.NOT_OVERWRITTEN.format(path=item.get("path"))
                                          for item in external if not item.get("overwritten")])),
    )
    blocks: list[Block] = [profile_line(profile)]
    for heading, lines in sections:
        if lines:
            blocks.extend([sentence(heading), *lines])
    return [*blocks, sentence(words.STOP_SERVER)]


def writes_text(mode: str, answer: Mapping[str, Any]) -> str | None:
    """Return what the restore writes: the change that it takes back with its time, or the date of the original."""
    if mode == "previous":
        replaces = _mapping(answer.get("replaces"))
        if not replaces:
            return None
        kind = str(replaces.get("operation_kind"))
        return words.PREVIOUS_WRITES.format(change=words.CHANGES.get(kind, kind),
                                            time=local_time(replaces.get("applied_at")))
    original = _mapping(answer.get("original"))
    return words.ORIGINAL_WRITES.format(time=local_time(original.get("captured_at"))) if original else None


def _refusals(answer: Mapping[str, Any]) -> list[Block]:
    """Return the refusals, and the 6.11 family text once when no refusal carries it."""
    messages = [str(item.get("message")) for item in _items(answer.get("refusals"))]
    family = _mapping(answer.get("family"))
    if family.get("verified") is False and isinstance(family.get("text"), str) and family["text"] not in messages:
        messages.append(family["text"])
    return [line for message in messages for line in _text(message)]


def _notices(mode: str, answer: Mapping[str, Any], original: Mapping[str, Any]) -> list[Block]:
    """Return the shared-original text or the intro line, the other profiles and the kept-changes sentence.

    The other profiles show only after a sentence that explains them. So a refused Restore original without an
    original shows no list (Product Owner 2026-10-15 21:42:25 in evidence 8.16).
    """
    explained = mode == "previous" or original.get("shared") is True
    profiles = [item.get("display_name") or DELETED_PROFILE.format(profile_id=item.get("profile_id"))
                for item in _items(answer.get("other_profiles"))] if explained else []
    lines = _text(original.get("text")) if original.get("shared") is True else []
    if profiles and mode == "previous":
        lines.append(sentence(INDENT, words.OTHER_PROFILES))
    return [*lines, *_list(None, profiles), *_text(original.get("kept_changes"))]


def _mapping(value: object) -> Mapping[str, Any]:
    """Return a mapping of the answer, or an empty one for a missing value."""
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> list[Mapping[str, Any]]:
    """Return the mappings of a list of the answer."""
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _text(text: object) -> list[Block]:
    """Return one backend or window text as an indented line, or nothing for a missing text."""
    return [sentence(INDENT, Value(text))] if isinstance(text, str) and text else []


def _list(label: str | None, rows: Sequence[object]) -> list[Block]:
    """Return a list with its label; a list without rows is not printed (window: list)."""
    shown = [str(row) for row in rows if row not in (None, "")]
    if not shown:
        return []
    head = [sentence(INDENT, label)] if label else []
    return [*head, *(sentence(ROW, Value(row)) for row in shown)]
