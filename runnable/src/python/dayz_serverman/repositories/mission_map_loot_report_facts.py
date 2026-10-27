"""Working-baseline facts that the loot candidate report needs beyond the candidates (leaf 6.9).

The facts are the categories, point counts and fixed dispatch of each
prototype group. They also include the categories that item types supply, the
positions of kept placements and the tier cells of each zone. Every function
is pure: the caller supplies the bytes, and nothing is written.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.mission_map_areaflags_candidate import parse_areaflags
from ..domain.mission_map_evaluation_shapes import loot_shape, split_spans
from .medical_zone_scan import Node
from .mission_map_mapgroup_read import (
    DIRT, POS, MapgroupRefused, read_cluster_placements, read_placements, read_prototype,
)


@dataclass(frozen=True)
class GroupFacts:
    """Categories and point counts of each container of one prototype group, and its fixed dispatch."""

    categories: dict[str, tuple[str, ...]]
    points: dict[str, int]
    fixed_dispatch: bool

    def all_categories(self) -> tuple[str, ...]:
        """Return every distinct category of the group in first-seen order; spelling and case stay exact."""
        return tuple(dict.fromkeys(name for names in self.categories.values() for name in names))


def group_facts(proto: bytes | None) -> dict[str, GroupFacts]:
    """Return the facts of each prototype group by its exact name; an unreadable file gives no facts.

    A name that two groups share exactly has no target (6.8 refuses it), so the first group is enough.
    """
    if proto is None:
        return {}
    try:
        prototype = read_prototype(proto)
    except MapgroupRefused:
        return {}
    result: dict[str, GroupFacts] = {}
    for node in prototype.groups:
        result.setdefault(node.attributes.get("name", ""), node_facts(node))
    return result


def node_facts(node: Node) -> GroupFacts:
    """Return the facts of one parsed prototype group."""
    categories, points = {}, {}
    for container in (child for child in node.children if child.tag == "container"):
        name = container.attributes.get("name", "")
        categories[name] = tuple(child.attributes.get("name", "") for child in container.children
                                 if child.tag == "category")
        points[name] = sum(1 for child in container.children if child.tag == "point")
    return GroupFacts(categories, points, any(child.tag == "dispatch" for child in node.children))


def supplied_categories(types: Sequence[bytes] | None) -> set[str] | None:
    """Return the categories that at least one item type with a nominal above 0 has.

    None means unknown: no types document was given, or one is not readable XML with integer nominals.
    """
    if not types:
        return None
    result: set[str] = set()
    for data in types:
        try:
            root = ET.fromstring(data)
            for item in root.iter("type"):
                nominal = item.findtext("nominal")
                if nominal is not None and int(nominal.strip()) > 0:
                    result.update(child.get("name", "") for child in item.findall("category"))
        except (ET.ParseError, ValueError):
            return None
    return result


def kept_positions(baseline: Mapping[str, bytes | None], clusters: Mapping[str, bytes] | None,
                   kept: Iterable[Any]) -> dict[tuple[str, int], tuple[float, float]]:
    """Return the X and Z of each kept placement by file and index; a placement that cannot be read is left out."""
    wanted = {(item.file, item.index) for item in kept}
    result: dict[tuple[str, int], tuple[float, float]] = {}
    for path in sorted({file for file, _index in wanted}):
        data = (clusters or {}).get(path) if path not in (POS, DIRT) else baseline.get(path)
        if data is None:
            continue
        try:
            if path in (POS, DIRT):
                placements = read_placements(path, data)[1]
            else:
                names = {item.name.casefold() for item in kept if item.file == path}
                placements = read_cluster_placements(path, data, names)
        except MapgroupRefused:
            continue
        for item in placements:
            if (path, item.index) in wanted and item.position is not None:
                result[(path, item.index)] = (item.position[0], item.position[2])
    return result


def tier_cells(plan: dict[str, Any], areaflags: bytes) -> dict[str, int]:
    """Return how many cells take the tier of each enabled explicit tier object, as the 6.6 candidate writes them.

    The highest covering object with a tier claims a cell; an inherit zone claims none (D9, R06).
    """
    shapes = [shape for item in plan["objects"] if (shape := loot_shape(item)) is not None and shape.tier is not None]
    counts = {shape.identifier: 0 for shape in shapes}
    if not shapes:
        return counts
    header = parse_areaflags(areaflags)
    grid = header.cell_grid()
    for row in range(header.rows):
        remaining = [(0, header.columns)]
        for shape in shapes:
            remaining, claimed = split_spans(remaining, shape.spans(grid, row))
            counts[shape.identifier] += sum(end - start for start, end in claimed)
            if not remaining:
                break
    return counts
