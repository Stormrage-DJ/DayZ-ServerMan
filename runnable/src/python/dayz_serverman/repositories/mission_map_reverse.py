"""Reverse the applied realization on the bytes of one writable file (8.7, Architect F2 of 2026-10-12 23:41:40).

Incorporation keeps the admin's edits of values that the editor does not own.
It removes what the editor wrote, so the working baseline never holds an
editor value:

- `mapgroupproto.xml`: generated groups are removed, and each ordinary
  ceiling goes back to its text, or its absence, in the working baseline.
- `mapgrouppos.xml`: each detached placement of the applied realization goes
  back, with its exact working-baseline bytes, before the root end tag.
- `mapgroupdirt.xml`: the placements of generated groups are removed.
- `env/zombie_territories.xml`: the marked editor block is removed.
- `areaflags.map`: the whole file is owned (D3), so nothing can be reversed.

Every change is a byte splice; no file is serialized again. The caller checks
the owned projection of the result against the working baseline, so a reversal
that misses an editor value refuses there. This module reads and writes no file.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..domain.medical_zone_rule import is_reserved
from .medical_zone_scan import Node, is_space
from .mission_map_infected import MARKER_BEGIN, MARKER_END
from .mission_map_mapgroup_read import DIRT, POS, PROTO, parse, read_placements
from .mission_map_mapgroup_splice import (
    ATTRIBUTE, TAG_NAME, Edit, apply_edits, attribute_edit, indent_of, insertion_edit, merge_removals, newline_of,
    removal_edit, with_newline,
)
from .mission_map_owned import TERRITORY_FILE
from .mission_map_terrain import AREAFLAGS_FILE


class ReversalError(ValueError):
    """Raised when the applied realization cannot be reversed on the bytes of a file."""


def reverse(path: str, data: bytes | None, baseline: bytes | None, groups: list[dict[str, Any]]) -> bytes | None:
    """Return the file bytes without the applied realization; None keeps an absent file absent."""
    if data is None or path == AREAFLAGS_FILE:
        return data
    if path == TERRITORY_FILE:
        return _without_block(data)
    if path == PROTO:
        if baseline is None:
            raise ReversalError("the working baseline has no mapgroupproto.xml")
        return _prototype(data, baseline)
    if path == DIRT:
        return _without_generated(data)
    if path == POS:
        return _with_detached(data, baseline, groups)
    raise ReversalError(f"{path} is not a writable file")


def _without_block(data: bytes) -> bytes:
    """Remove the marked editor block with its own lines; a file without markers stays as it is."""
    start, end = data.find(MARKER_BEGIN), data.find(MARKER_END)
    if start < 0 and end < 0:
        return data
    if start < 0 or end < start:
        raise ReversalError("the editor markers are damaged")
    end += len(MARKER_END)
    line_start = data.rfind(b"\n", 0, start) + 1
    if is_space(data[line_start:start]):
        start = line_start
        line_end = data.find(b"\n", end)
        if line_end >= 0 and is_space(data[end:line_end]):
            end = line_end + 1
    return data[:start] + data[end:]


def _prototype(data: bytes, baseline: bytes) -> bytes:
    """Remove generated groups and give each ordinary ceiling its working-baseline text or absence."""
    root, base = parse(PROTO, data), parse(PROTO, baseline)
    reserved = [removal_edit(data, root, index) for index, node in enumerate(root.children)
                if node.tag == "group" and is_reserved(node.attributes.get("name", ""))]
    edits = merge_removals(data, root, reserved)
    # Groups pair by exact name and by their order among the groups of that name
    partners: dict[str, list[Node]] = {}
    for node in (child for child in base.children if child.tag == "group"):
        partners.setdefault(node.attributes.get("name", ""), []).append(node)
    seen: Counter[str] = Counter()
    for node in (child for child in root.children if child.tag == "group"):
        name = node.attributes.get("name", "")
        if is_reserved(name):
            continue
        candidates = partners.get(name, [])
        if seen[name] >= len(candidates):
            raise ReversalError(f"group {name} is not in the working baseline")
        partner = candidates[seen[name]]
        seen[name] += 1
        edits += _ceiling(data, node, partner)
        containers = [child for child in node.children if child.tag == "container"]
        others = [child for child in partner.children if child.tag == "container"]
        if [item.attributes.get("name") for item in containers] != [item.attributes.get("name") for item in others]:
            raise ReversalError(f"group {name} has other containers than the working baseline")
        for container, other in zip(containers, others):
            edits += _ceiling(data, container, other)
    return _apply(data, edits)


def _ceiling(data: bytes, node: Node, partner: Node) -> list[Edit]:
    """Return the edit that gives one element the ceiling text, or the absence, of its partner."""
    current, wanted = node.attributes.get("lootmax"), partner.attributes.get("lootmax")
    if current == wanted:
        return []
    if wanted is None:
        return [_removal(data, node, "lootmax")]
    try:
        return [attribute_edit(data, node, "lootmax", wanted)]
    except UnicodeEncodeError as error:
        raise ReversalError("a ceiling text is not ASCII") from error


def _removal(data: bytes, node: Node, name: str) -> Edit:
    """Return the edit that removes one attribute, with the white space before it, from a start tag."""
    tag = data[node.start:node.tag_end]
    match = TAG_NAME.match(tag)
    position = 0 if match is None else match.end()
    while (found := ATTRIBUTE.match(tag, position)) is not None:
        if found.group(1) == name.encode("ascii"):
            return node.start + found.start(), node.start + found.end(), b""
        position = found.end()
    raise ReversalError(f"attribute {name} was not found")


def _without_generated(data: bytes) -> bytes:
    """Remove every top-level placement whose name is a reserved editor name."""
    root, placements = read_placements(DIRT, data)
    positions = {id(child): index for index, child in enumerate(root.children)}
    removals = [removal_edit(data, root, positions[id(item.node)]) for item in placements if is_reserved(item.name)]
    return _apply(data, merge_removals(data, root, removals)) if removals else data


def _with_detached(data: bytes, baseline: bytes | None, groups: list[dict[str, Any]]) -> bytes:
    """Put each recorded detached placement back before the root end tag, with its working-baseline bytes."""
    wanted: Counter[tuple[object, ...]] = Counter()
    for group in groups:
        for binding in group["detached"]:
            wanted[_binding(binding["name"], binding["position"], binding["rotation"], binding["angle"])] += \
                binding["multiplicity"]
    if not wanted:
        return data
    if baseline is None:
        raise ReversalError("the working baseline has no mapgrouppos.xml")
    _base_root, base_placements = read_placements(POS, baseline)
    chosen = []
    for item in base_placements:
        key = _binding(item.name, item.position, item.rotation, item.angle)
        if wanted[key] > 0:
            wanted[key] -= 1
            chosen.append(item.node)
    if any(wanted.values()):
        raise ReversalError("a detached placement is not in the working baseline")
    root, placements = read_placements(POS, data)
    try:
        newline = newline_of(data)
        first = placements[0].node if placements else None
        indent = indent_of(data, first) if first is not None else indent_of(baseline, chosen[0])
        lines = [indent + with_newline(baseline[node.start:node.end], newline) for node in chosen]
        return apply_edits(data, [insertion_edit(data, root, lines, newline)])
    except ValueError as error:
        raise ReversalError(str(error)) from error


def _binding(name: object, position: object, rotation: object, angle: object) -> tuple[object, ...]:
    """Return the comparison form of one placement binding, numbers as floats."""
    def vector(value: object) -> tuple[float, ...] | None:
        return None if value is None else tuple(float(part) for part in value)  # type: ignore[union-attr]
    return name, vector(position), vector(rotation), None if angle is None else float(angle)  # type: ignore[arg-type]


def _apply(data: bytes, edits: list[Edit]) -> bytes:
    """Apply the splices, or refuse when two of them overlap."""
    try:
        return apply_edits(data, edits)
    except ValueError as error:
        raise ReversalError(str(error)) from error
