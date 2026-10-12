"""Immutable loot geometry and exact closed-boundary row spans for D9 evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .mission_map_evaluation_scanline import ScanEdge, polygon_spans, scan_edges
from .mission_map_geometry import CellGrid, circle_covers, polygon_covers


@dataclass(frozen=True)
class LootShape:
    """Geometry and independent loot properties of a standalone or linked area."""

    identifier: str
    name: str
    kind: str
    hidden: bool
    tier: str | None
    abundance: str
    centre: tuple[float, float] | None
    radius: float | None
    vertices: tuple[tuple[float, float], ...]
    _box: tuple[float, float, float, float] = field(init=False, repr=False)
    _edges: tuple[ScanEdge, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Cache the immutable bounding box once for repeated row pruning."""
        if self.vertices:
            xs, zs = zip(*self.vertices)
            box = min(xs), min(zs), max(xs), max(zs)
        else:
            x, z = self.centre
            box = x - self.radius, z - self.radius, x + self.radius, z + self.radius
        object.__setattr__(self, "_box", box)
        object.__setattr__(self, "_edges", scan_edges(self.vertices))

    def covers(self, x: float, z: float) -> bool:
        """Use the shared exact polygon or double-precision circle predicate."""
        if self.vertices:
            return polygon_covers(self.vertices, x, z)
        return circle_covers(*self.centre, self.radius, x, z)

    def extent(self) -> tuple[float, float, float, float]:
        """Return the closed native bounding box, including circle tangencies."""
        return self._box

    def spans(self, grid: CellGrid, row: int) -> list[tuple[int, int]]:
        """Return disjoint covered column intervals with exclusive right ends."""
        z = grid.centre(0, row)[1]
        left, bottom, right, top = self.extent()
        if not bottom <= z <= top:
            return []
        # Circle rows have one interval; polygon rows can have several.
        if self.vertices:
            return polygon_spans(self.vertices, self._edges, grid, row)
        else:
            dz = z - self.centre[1]
            squared = self.radius * self.radius - dz * dz
            if squared < 0:
                return []
            reach = math.sqrt(squared)
            intervals = [(self.centre[0] - reach, self.centre[0] + reach)]
        # Test intersection bands exactly; keep only the safe interval interior.
        spans = []
        for start, end in intervals:
            first = math.ceil((start - grid.bounds.x_min) / grid.cell_width - 0.5)
            last = math.floor((end - grid.bounds.x_min) / grid.cell_width - 0.5)
            core_start, core_end = max(0, first + 2), min(grid.columns, last - 1)
            if core_start < core_end:
                spans.append((core_start, core_end))
            bands = set(range(max(0, first - 2), min(grid.columns, first + 2)))
            bands.update(range(max(0, last - 1), min(grid.columns, last + 3)))
            for column in sorted(bands):
                if self.covers(*grid.centre(column, row)):
                    spans.append((column, column + 1))
        return merge_spans(spans)


def loot_shape(item: dict[str, Any], *, include_disabled: bool = False) -> LootShape | None:
    """Extract linked loot independently of encounter exclusion or provider support."""
    properties = item
    radius = item.get("radius")
    if item["kind"] == "encounter":
        properties = item["linked_loot"]
        if not properties["enabled"] and not include_disabled:
            return None
        radius = properties["radius"] if radius is None else radius
    centre = item.get("centre")
    vertices = tuple((p["x"], p["z"]) for p in item.get("vertices", ()))
    return LootShape(item["id"], item["name"], item["kind"], not item["visible"],
                     properties["tier"], properties["abundance"],
                     None if centre is None else (centre["x"], centre["z"]), radius or 0.0, vertices)


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union overlapping or adjacent exclusive-ended intervals."""
    result: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def split_spans(spans: list[tuple[int, int]], cutters: list[tuple[int, int]]) -> tuple[list, list]:
    """Return uncovered and intersecting intervals when cutters claim priority."""
    remainder, claimed = [], []
    for start, end in spans:
        cursor = start
        for low, high in cutters:
            if high <= cursor:
                continue
            if low >= end:
                break
            # Preserve the gap before each winning interval.
            if cursor < low:
                remainder.append((cursor, min(low, end)))
            claim_end = min(high, end)
            if max(cursor, low) < claim_end:
                claimed.append((max(cursor, low), claim_end))
            cursor = max(cursor, claim_end)
        if cursor < end:
            remainder.append((cursor, end))
    return remainder, claimed
