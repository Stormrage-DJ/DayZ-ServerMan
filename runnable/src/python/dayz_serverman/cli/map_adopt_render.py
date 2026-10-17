"""Review blocks of the adoption answer for `map adopt` (leaf 10.36, design D12 and D14).

The sections and their order are those of the adoption dialog of the UI-UX Designer (2026-10-16 14:21:59 in
evidence 10.1, section 4): refusals, what happened, what adoption writes, what stays unchanged. A refused answer
shows only the first two. A section without content is not printed. There is no footer line (ADOPT-19 is empty).

The texts of the case and route come with the answer and print unchanged (PO-6). The headings and the list label
are the Product Owner texts ADOPT-10, ADOPT-11, ADOPT-14, ADOPT-15 and ADOPT-17 (2026-10-16 14:26:38 in evidence
10.1). Leaf 10.16 puts them into the window.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .commands.common import profile_line
from .map_publish_wording import DELETED_PROFILE
from .map_review_render import INDENT, ROW
from .output import Block, Value, sentence

# ADOPT-10, ADOPT-11, ADOPT-14 and ADOPT-17: the section headings; ADOPT-15: the label of the record list
REFUSALS_HEADING = "Adoption is not possible"
HAPPENED_HEADING = "What happened"
WRITES_HEADING = "What adoption writes"
RECORDS_LABEL = "Records that adoption writes"
UNCHANGED_HEADING = "What stays unchanged"


def adopt_blocks(profile: Mapping[str, Any] | None, answer: Mapping[str, Any]) -> list[Block]:
    """Return the review of one adoption answer: the profile line, then the sections of the dialog."""
    refusals = [item.get("message") for item in _items(answer.get("refusals"))]
    profiles = [item.get("display_name") or DELETED_PROFILE.format(profile_id=item.get("profile_id"))
                for item in _items(answer.get("other_profiles"))]
    happened = [*_text(answer.get("happened")), *_text(answer.get("other_profiles_text")), *_rows(profiles)]
    records = [str(row) for row in answer.get("records") or [] if row]
    writes = [*_text(answer.get("intro")), *([sentence(INDENT, RECORDS_LABEL), *_rows(records)] if records else [])]
    unchanged = [line for text in answer.get("unchanged") or [] for line in _text(text)]
    sections = [(REFUSALS_HEADING, [line for text in refusals for line in _text(text)]),
                (HAPPENED_HEADING, happened)]
    if answer.get("refused") is not True:
        sections += [(WRITES_HEADING, writes), (UNCHANGED_HEADING, unchanged)]
    blocks: list[Block] = [profile_line(profile)]
    for heading, lines in sections:
        if lines:
            blocks.extend([sentence(heading), *lines])
    return blocks


def _items(value: object) -> list[Mapping[str, Any]]:
    """Return the mappings of a list of the answer."""
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _text(text: object) -> list[Block]:
    """Return one text of the answer as an indented line, or nothing for a missing text."""
    return [sentence(INDENT, Value(text))] if isinstance(text, str) and text else []


def _rows(rows: list[Any]) -> list[Block]:
    """Return the rows of a list, one more indent in."""
    return [sentence(ROW, Value(str(row))) for row in rows if row not in (None, "")]
