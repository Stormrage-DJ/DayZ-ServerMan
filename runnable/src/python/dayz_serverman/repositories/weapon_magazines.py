"""Localized magazine chance edits of `cfgspawnabletypes.xml` with the original chances kept in the file.

A magazine block is an `attachments` element with a `chance` whose items are all magazines (`Mag_`).
Its chance is the chance that the weapon spawns with one of these magazines. The file keeps one
marker comment after the `<spawnabletypes>` tag while the percentage is not 100. The marker holds
the percentage and the original chance text of every magazine block. Each change starts from the
originals, so changes never compound. 100 puts back the original bytes.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..domain.loot_supply import (
    MAGAZINE_KEY, ORIGINAL_PERCENT, LootSupplyError, scale_chance, validate_percent,
)
from .configuration_common import ConfigurationFileError
from .mission_xml import validate_xml

# Comments, CDATA and type elements, so a commented-out type is never read or changed
TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<type\b[^>]*?/>|<type\b[^>]*>.*?</type\s*>", re.DOTALL)
# Comments and attachments elements inside one type block
INNER = re.compile(r"<!--.*?-->|<attachments\b[^>]*?/>|<attachments\b[^>]*>.*?</attachments\s*>", re.DOTALL)
# The chance attribute of an opening attachments tag
CHANCE = re.compile(r"""(\bchance\s*=\s*)(["'])([^"']*)(\2)""")
# The marker comment, with the line break and indent that its insertion added
MARKER = re.compile(r"(?:\r?\n[ \t]*)?<!-- DayZ-ServerMan magazine chance: (\{.*?\}) -->", re.DOTALL)
MARKER_TEXT = "<!-- DayZ-ServerMan magazine chance: {} -->"
# The opening tag of the document element
ROOT_TAG = re.compile(r"<spawnabletypes\b[^>]*>")
# A plain chance number
NUMBER = re.compile(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)")


@dataclass(frozen=True)
class MagazineBlock:
    """One magazine block: its key, the span of its chance text, and its current and original chance text."""

    key: str
    start: int
    end: int
    current: str
    original: str


def read_magazines(text: str) -> tuple[int, tuple[MagazineBlock, ...]]:
    """Return the magazine percentage and the magazine blocks of a spawnable types document, or raise."""
    root = validate_xml(text)
    if root.tag != "spawnabletypes":
        raise ConfigurationFileError("spawnable types document has no spawnabletypes element")
    stored = _marker(text)
    percent = stored.get("percent", ORIGINAL_PERCENT)
    originals: dict[str, str] = stored.get("original", {})
    types = [match for match in TOKEN.finditer(text) if match.group(0).startswith("<type")]
    if len(types) != sum(1 for _ in root.iter("type")):
        raise ConfigurationFileError("spawnable types document has type elements that cannot be edited safely")
    names = [_name(match.group(0)) for match in types]
    blocks: list[MagazineBlock] = []
    for match, name in zip(types, names):
        # A repeated weapon name has no unique key, so its blocks stay as they are
        if not name or names.count(name) > 1:
            continue
        index = 0
        for inner in INNER.finditer(match.group(0)):
            raw = inner.group(0)
            if raw.startswith("<!--"):
                continue
            index += 1
            chance = CHANCE.search(raw[:raw.index(">") + 1])
            if chance is None or not _magazines_only(raw) or NUMBER.fullmatch(chance.group(3)) is None:
                continue
            key = f"{name}#{index}"
            current = chance.group(3)
            start = match.start() + inner.start() + chance.start(3)
            blocks.append(MagazineBlock(key, start, start + len(current), current,
                                        _original(current, originals.get(key), percent)))
    return percent, tuple(blocks)


def read_magazine_values(text: str) -> dict[str, int]:
    """Return the magazine chance percentage."""
    return {MAGAZINE_KEY: read_magazines(text)[0]}


def patch_magazines(text: str, percent: int) -> str:
    """Return the document with the percentage applied to the original chance of each magazine block."""
    _current, blocks = read_magazines(text)
    output: list[str] = []
    cursor = 0
    for block in blocks:
        wanted = _wanted(block.original, percent)
        # Another spelling of the same number stays, except that 100 restores the original text
        if wanted == block.current or (percent != ORIGINAL_PERCENT and float(wanted) == float(block.current)):
            continue
        output.extend((text[cursor:block.start], wanted))
        cursor = block.end
    output.append(text[cursor:])
    return _write_marker("".join(output), percent, blocks)


def _original(current: str, stored: str | None, percent: int) -> str:
    """Return the original chance text; a chance edited by hand counts as already scaled."""
    if stored is None:
        return current
    if float(_wanted(stored, percent)) == float(current):
        return stored
    # Edited by hand: at 100 the edit is the original; at 0 nothing can be derived from it
    if percent in (0, ORIGINAL_PERCENT):
        return stored if percent == 0 else current
    return _render(min(1.0, float(current) * 100 / percent))


def _wanted(original: str, percent: int) -> str:
    """Return the chance text that a percentage gives; 100 gives the original text."""
    return original if percent == ORIGINAL_PERCENT else _render(scale_chance(float(original), percent))


def _render(value: float) -> str:
    """Return a chance as short decimal text."""
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _name(raw: str) -> str:
    """Return the name of one raw type block."""
    try:
        return ET.fromstring(raw).get("name") or ""
    except ET.ParseError as error:
        raise ConfigurationFileError("spawnable types document has a type that cannot be read") from error


def _magazines_only(raw: str) -> bool:
    """Return whether an attachments block lists items and every item is a magazine."""
    try:
        element = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ConfigurationFileError("spawnable types document has attachments that cannot be read") from error
    items = [item.get("name") or "" for item in element.findall("item")]
    return bool(items) and all(name.lower().startswith("mag_") for name in items)


def _marker(text: str) -> dict:
    """Return the stored percentage and original chances; no marker means originals."""
    matches = list(MARKER.finditer(text))
    if not matches:
        return {}
    if len(matches) > 1:
        raise ConfigurationFileError("spawnable types document has more than one magazine chance marker")
    try:
        stored = json.loads(matches[0].group(1))
        if (not isinstance(stored, dict) or stored.get("version") != 1
                or not isinstance(stored.get("original"), dict)
                or not all(isinstance(value, str) and NUMBER.fullmatch(value)
                           for value in stored["original"].values())):
            raise ValueError
        validate_percent(stored.get("percent"), MAGAZINE_KEY)
    except (ValueError, LootSupplyError) as error:
        raise ConfigurationFileError("magazine chance marker is malformed") from error
    return stored


def _write_marker(text: str, percent: int, blocks: tuple[MagazineBlock, ...]) -> str:
    """Remove the marker, then add a new one while the percentage is not 100."""
    found = list(MARKER.finditer(text))
    if found:
        text = text[:found[0].start()] + text[found[0].end():]
    if percent == ORIGINAL_PERCENT:
        return text
    payload = json.dumps({"version": 1, "percent": percent,
                          "original": {block.key: block.original for block in blocks}},
                         separators=(",", ":"), ensure_ascii=True)
    # A comment must not hold a double hyphen, so such a name cannot be stored
    if "--" in payload:
        raise ConfigurationFileError("a weapon name cannot be stored in the magazine chance marker")
    root = ROOT_TAG.search(text)
    if root is None:
        raise ConfigurationFileError("spawnable types document has no spawnabletypes element")
    newline = "\r\n" if "\r\n" in text else "\n"
    indent = re.search(r"\n([ \t]*)<type\b", text)
    insertion = f"{newline}{indent.group(1) if indent else ''}{MARKER_TEXT.format(payload)}"
    return text[:root.end()] + insertion + text[root.end():]
