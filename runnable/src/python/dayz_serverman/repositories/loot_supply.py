"""Localized loot supply edits of `db/types.xml` with the original counts kept in the file.

The file keeps one marker comment after the `<types>` tag while any percentage is not 100. The
marker holds the percentages and the original counts of every managed item type, so each change is
computed from the originals and never compounds. Without the marker the file holds the originals.
When every percentage returns to 100, the original values and the original bytes around the marker
come back. Only the values of managed types change; comments, layout and other types stay.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from ..domain.loot_supply import (
    FILL_GROUPS, FILL_KEY, ORIGINAL_PERCENT, SUPPLY_GROUPS, SUPPLY_KEYS, LootSupplyError, scale_counts,
    scale_fill, supply_group, supply_key, unscale_count, validate_percent,
)
from .configuration_common import ConfigurationFileError
from .mission_xml import validate_xml

# Comments, CDATA and type elements, so a commented-out type is never read or changed
TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<type\b[^>]*?/>|<type\b[^>]*>.*?</type\s*>", re.DOTALL)
# The marker comment, with the line break and indent that its insertion added
MARKER = re.compile(r"(?:\r?\n[ \t]*)?<!-- DayZ-ServerMan loot supply: (\{.*?\}) -->", re.DOTALL)
MARKER_TEXT = "<!-- DayZ-ServerMan loot supply: {} -->"
# The opening tag of the document element
ROOT_TAG = re.compile(r"<types\b[^>]*>")
# The four count values of an item type: nominal, min, quantmin, quantmax
VALUE_TAGS = ("nominal", "min", "quantmin", "quantmax")

Counts = tuple[int, int, int | None, int | None]


@dataclass(frozen=True)
class SupplyItem:
    """One managed item type: its group, its span in the text, its current and its original counts."""

    name: str
    group: str
    start: int
    end: int
    current: Counts
    original: Counts


@dataclass(frozen=True)
class SupplyState:
    """The percentages, the managed item types and the facts of one types document."""

    percents: dict[str, int]
    items: tuple[SupplyItem, ...]
    marker: tuple[int, int] | None
    edited: int
    duplicates: int

    def facts(self) -> dict[str, Any]:
        """Return the item type count and the original nominal sum of each group."""
        groups = {supply_key(group): {"types": 0, "nominal": 0} for group in SUPPLY_GROUPS}
        for item in self.items:
            groups[supply_key(item.group)]["types"] += 1
            groups[supply_key(item.group)]["nominal"] += item.original[0]
        fill = [item for item in self.items if item.group in FILL_GROUPS and item.original[2] is not None]
        return {"groups": groups, "fill_types": len(fill), "edited": self.edited, "duplicates": self.duplicates}


def read_supply(text: str) -> SupplyState:
    """Read the percentages and the managed item types of a types document, or raise."""
    root = validate_xml(text)
    if root.tag != "types":
        raise ConfigurationFileError("types document has no types element")
    stored, marker = _marker(text)
    percents = {key: ORIGINAL_PERCENT for key in SUPPLY_KEYS}
    percents.update(stored.get("percent", {}))
    originals: dict[str, list[Any]] = stored.get("original", {})
    blocks = [match for match in TOKEN.finditer(text) if match.group(0).startswith("<type")]
    # The raw blocks and the parsed elements must agree, so every edit has an exact place
    if len(blocks) != sum(1 for _ in root.iter("type")):
        raise ConfigurationFileError("types document has type elements that cannot be edited safely")
    parsed = [(match, _element(match.group(0))) for match in blocks]
    names = [element.get("name") or "" for _match, element in parsed]
    repeated = {name for name in names if names.count(name) > 1}
    items: list[SupplyItem] = []
    edited = 0
    for match, element in parsed:
        name = element.get("name") or ""
        category = element.find("category")
        group = supply_group(name, None if category is None else category.get("name"))
        current = _counts(element)
        if group is None or name in repeated or current is None:
            continue
        original, changed = _original(current, originals.get(name), group, percents)
        edited += changed
        # A type that spawns nothing at its original values is not managed
        if original[0] <= 0:
            continue
        items.append(SupplyItem(name, group, match.start(), match.end(), current, original))
    return SupplyState(percents, tuple(items), marker, edited, len(repeated))


def read_supply_values(text: str) -> dict[str, int]:
    """Return the percentage of every supply group and the fill percentage."""
    return dict(read_supply(text).percents)


def patch_supply(text: str, updates: dict[str, int]) -> str:
    """Return the document with the new percentages applied to the original counts of each managed type."""
    state = read_supply(text)
    percents = {**state.percents, **updates}
    output: list[str] = []
    cursor = 0
    # Rewrite only the count values that differ from the scaled originals
    for item in state.items:
        wanted = scaled(item.original, item.group, percents)
        if wanted == item.current:
            continue
        output.append(text[cursor:item.start])
        output.append(_patch_block(text[item.start:item.end], item.current, wanted, item.name))
        cursor = item.end
    output.append(text[cursor:])
    result = "".join(output)
    return _write_marker(result, percents, state.items)


def scaled(original: Counts, group: str, percents: dict[str, int]) -> Counts:
    """Return the counts that the percentages give for the original counts of one group."""
    nominal, minimum = scale_counts(original[0], original[1], percents[supply_key(group)])
    low, high = original[2], original[3]
    if group in FILL_GROUPS and low is not None and high is not None:
        low, high = scale_fill(low, high, percents[FILL_KEY])
    return nominal, minimum, low, high


def _original(current: Counts, stored: list[Any] | None, group: str,
              percents: dict[str, int]) -> tuple[Counts, int]:
    """Return the original counts of one type and 1 when its values were edited by hand, else 0.

    A type without stored originals holds its originals: it is new, or no percentage was set. A type
    whose values differ from its scaled originals was edited by hand. Its values count as already
    scaled. The next change scales from the matching original, so the edit is not scaled twice.
    """
    if stored is None:
        return current, 0
    original = _stored_counts(stored)
    if scaled(original, group, percents) == current:
        return original, 0
    fill = percents[FILL_KEY] if group in FILL_GROUPS else ORIGINAL_PERCENT
    low, high = current[2], current[3]
    percent = percents[supply_key(group)]
    return (unscale_count(current[0], percent), unscale_count(current[1], percent),
            None if low is None else unscale_count(low, fill) if low >= 0 else low,
            None if high is None else unscale_count(high, fill) if high >= 0 else high), 1


def _element(raw: str) -> ET.Element:
    """Parse one raw type block."""
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise ConfigurationFileError("types document has a type element that cannot be read") from error


def _counts(element: ET.Element) -> Counts | None:
    """Return the counts of one type, or None when its nominal or min is missing, repeated or not a number."""
    values: list[int | None] = []
    for tag in VALUE_TAGS:
        nodes = element.findall(tag)
        if len(nodes) != 1:
            if tag in ("nominal", "min"):
                return None
            values.append(None)
            continue
        raw = (nodes[0].text or "").strip()
        if re.fullmatch(r"-?[0-9]+", raw) is None:
            if tag in ("nominal", "min"):
                return None
            values.append(None)
            continue
        values.append(int(raw))
    return values[0], values[1], values[2], values[3]  # type: ignore[return-value]


def _stored_counts(stored: list[Any]) -> Counts:
    """Return stored original counts after a strict shape check."""
    if (not isinstance(stored, list) or len(stored) != 4
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in stored[:2])
            or not all(value is None or (isinstance(value, int) and not isinstance(value, bool))
                       for value in stored[2:])):
        raise ConfigurationFileError("loot supply marker has malformed original values")
    return stored[0], stored[1], stored[2], stored[3]


def _patch_block(block: str, current: Counts, wanted: Counts, name: str) -> str:
    """Replace each changed count value inside one type block."""
    for tag, before, after in zip(VALUE_TAGS, current, wanted):
        if before == after:
            continue
        pattern = re.compile(rf"(<{tag}>\s*)-?[0-9]+(\s*</{tag}>)")
        block, count = pattern.subn(lambda match: f"{match.group(1)}{after}{match.group(2)}", block)
        if count != 1:
            raise ConfigurationFileError(f"type {name}.{tag} cannot be patched safely")
    return block


def _marker(text: str) -> tuple[dict[str, Any], tuple[int, int] | None]:
    """Return the stored percentages and originals and the marker span; no marker means originals."""
    matches = list(MARKER.finditer(text))
    if not matches:
        return {}, None
    if len(matches) > 1:
        raise ConfigurationFileError("types document has more than one loot supply marker")
    try:
        stored = json.loads(matches[0].group(1))
    except json.JSONDecodeError as error:
        raise ConfigurationFileError("loot supply marker is malformed") from error
    if not isinstance(stored, dict) or stored.get("version") != 1:
        raise ConfigurationFileError("loot supply marker is malformed")
    percent, original = stored.get("percent"), stored.get("original")
    if not isinstance(percent, dict) or not isinstance(original, dict) or not set(percent) <= SUPPLY_KEYS:
        raise ConfigurationFileError("loot supply marker is malformed")
    try:
        for key, value in percent.items():
            validate_percent(value, key)
    except LootSupplyError as error:
        raise ConfigurationFileError("loot supply marker is malformed") from error
    return stored, (matches[0].start(), matches[0].end())


def _write_marker(text: str, percents: dict[str, int], items: tuple[SupplyItem, ...]) -> str:
    """Remove the marker, then add a new one while any percentage is not 100."""
    found = list(MARKER.finditer(text))
    if found:
        text = text[:found[0].start()] + text[found[0].end():]
    changed = {key: value for key, value in sorted(percents.items()) if value != ORIGINAL_PERCENT}
    if not changed:
        return text
    payload = json.dumps({"version": 1, "percent": changed,
                          "original": {item.name: list(item.original) for item in items}},
                         separators=(",", ":"), ensure_ascii=True)
    # A comment must not hold a double hyphen, so such a name cannot be stored
    if "--" in payload:
        raise ConfigurationFileError("an item type name cannot be stored in the loot supply marker")
    root = ROOT_TAG.search(text)
    if root is None:
        raise ConfigurationFileError("types document has no types element")
    newline = "\r\n" if "\r\n" in text else "\n"
    indent = re.search(r"\n([ \t]*)<type\b", text)
    insertion = f"{newline}{indent.group(1) if indent else ''}{MARKER_TEXT.format(payload)}"
    return text[:root.end()] + insertion + text[root.end():]
