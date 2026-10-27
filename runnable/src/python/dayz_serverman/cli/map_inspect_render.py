"""Text blocks of `map inspect` (leaf 10.4, design D12): the point answer and the object answer as lines."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.mission_map_loot_report_texts import CAPACITY_REASON_TEXTS
from . import map_inspect_wording as words
from .commands.common import profile_line, yes_no
from .map_render import number_text
from .map_wording import ZONE_PRESETS
from .output import Block, Echo, TypeText, Value, sentence


def inspect_blocks(profile: Mapping[str, Any] | None, result: Mapping[str, Any]) -> list[Block]:
    """Return the lines of the final answer: an object answer names its ID, a point answer its coordinates."""
    body = _object(result) if "object_id" in result else _point(result)
    return [profile_line(profile), *body]


def _point(result: Mapping[str, Any]) -> list[Block]:
    """Return the point answer: place, tiers with their source, the zone that wins, capacity and encounters."""
    grid = result.get("grid") if isinstance(result.get("grid"), Mapping) else {}
    cell = result.get("header_cell") if isinstance(result.get("header_cell"), Mapping) else None
    cell_text = f"Column {cell.get('i')}, row {cell.get('j')}" if cell else words.OUTSIDE_CELL
    blocks: list[Block] = [
        sentence(f"{words.POINT_LABEL}: X {number_text(result.get('x'))} m, Z {number_text(result.get('z'))} m"),
        sentence(f"{words.GRID_LABEL}: X {number_text(grid.get('h'), 4)}, Z {number_text(grid.get('v'), 4)}"),
        sentence(f"{words.CELL_LABEL}: {cell_text}"),
        sentence(f"{words.ORIGINAL_LABEL}: {_tiers(result.get('original_tier'))}"),
        sentence(f"{words.EFFECTIVE_LABEL}: {_effective(result)}")]
    stopped = result.get("not_writable") if isinstance(result.get("not_writable"), Mapping) else None
    if stopped is not None:
        before, rest = words.NOT_WRITABLE_ZONE.split("{name}")
        after = rest.format(tier=stopped.get("tier"))
        blocks.append(sentence(before, Value(str(stopped.get("name"))), after))
    blocks.append(_set_by(result))
    abundance = result.get("abundance") if isinstance(result.get("abundance"), Mapping) else {}
    zone = f"zone {ZONE_PRESETS.get(str(abundance.get('zone_preset')), words.signed(abundance.get('zone') or 0))}" \
        if abundance.get("zone_preset") else words.NO_ZONE
    blocks.append(sentence(words.CAPACITY_CHANGE.format(
        global_value=words.signed(abundance.get("global") or 0), zone=zone,
        effective=words.signed(abundance.get("effective") or 0))))
    hidden = result.get("hidden_contributors") or []
    if hidden:
        blocks.append(sentence(words.HIDDEN_ONE if len(hidden) == 1 else words.HIDDEN_MANY.format(count=len(hidden))))
    blocks.append(sentence(words.ENCOUNTERS_HEADING))
    names = [str(item.get("name")) for item in result.get("encounters") or [] if isinstance(item, Mapping)]
    if names:
        blocks.extend(sentence("  ", Value(name)) for name in names)
    else:
        blocks.append(sentence("  ", words.NONE_TEXT))
    reason = CAPACITY_REASON_TEXTS.get(str(result.get("capacity_reason")))
    return blocks + ([sentence(reason)] if reason else [])


def _tiers(tiers: object) -> str:
    """Return a tier set as text; a set that was not read is unknown."""
    return words.UNKNOWN_TEXT if tiers is None else words.tier_text(list(tiers))


def _effective(result: Mapping[str, Any]) -> str:
    """Return the effective tiers with their source: a zone that sets them, or the original tier."""
    tiers = _tiers(result.get("effective_tier"))
    if result.get("effective_tier") is None:
        return words.UNKNOWN_TEXT
    if result.get("tier_source") == "zone":
        source = result.get("tier_object") if isinstance(result.get("tier_object"), Mapping) else {}
        return f"{tiers} from {source.get('name')}"
    return f"{tiers} (original)" if result.get("tier_source") == "original" else words.UNKNOWN_TEXT


def _set_by(result: Mapping[str, Any]) -> Block:
    """Return the zone that wins at the point, or the original tier when no zone covers it."""
    winner = result.get("winner") if isinstance(result.get("winner"), Mapping) else None
    if winner is None:
        return sentence(f"{words.SET_BY_LABEL}: {words.ORIGINAL_TIER}")
    text = f"{winner.get('name')}{' (hidden on the map)' if winner.get('hidden') else ''}"
    capacity_only = f". {words.CAPACITY_ONLY}" if result.get("abundance_only") else ""
    return sentence(f"{words.SET_BY_LABEL}: ", Value(text), capacity_only)


def _object(result: Mapping[str, Any]) -> list[Block]:
    """Return the object answer: its tiers, its cells, tier writes and the building groups that it covers."""
    before, after = words.CELLS_LABEL.split("{basis}")
    blocks: list[Block] = [
        sentence(f"{words.OBJECT_LABEL}: ", Echo(str(result.get("object_id")))),
        sentence(f"{words.EFFECTIVE_LABEL}: {words.tier_text(result.get('effective_tiers'))}"),
        sentence(f"{words.ORIGINAL_LABEL}: {words.tier_text(result.get('original_tiers'))}"),
        sentence(before.format(count=result.get("cell_count")),
                 words.CELL_BASIS.get(str(result.get("cells")), str(result.get("cells"))), after),
        sentence(f"{words.TIER_WRITABLE_LABEL}: {yes_no(result.get('tier_writable'))}")]
    if not result.get("tier_writable"):
        before, after = words.WRITES_REASON_LINE.split("{command}")
        blocks.extend((sentence(words.WRITES_CAUSE), sentence(before, TypeText("map show"), after)))
    entries = [entry for entry in result.get("capacity") or [] if isinstance(entry, Mapping)]
    blocks.append(sentence(f"{words.GROUPS_LABEL}:"))
    blocks.extend(sentence("  ", Value(words.capacity_entry_text(dict(entry)))) for entry in entries) if entries \
        else blocks.append(sentence("  ", words.CAPACITY_NONE))
    return blocks
