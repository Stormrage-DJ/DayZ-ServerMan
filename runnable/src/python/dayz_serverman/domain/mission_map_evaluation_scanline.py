"""Exact edge-local integer transitions for closed polygon cell-centre coverage."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache

from .mission_map_geometry import CellGrid, Point, _orientation


@dataclass(frozen=True)
class ScanEdge:
    """Retain exact float endpoints, closed row extent and upward/downward direction."""

    start: Point
    end: Point
    low_z: float
    high_z: float
    direction: int


def scan_edges(vertices: tuple[Point, ...]) -> tuple[ScanEdge, ...]:
    """Prepare immutable edges, including horizontal boundary-only segments."""
    edges = []
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        edges.append(ScanEdge(start, end, min(start[1], end[1]), max(start[1], end[1]),
                              (end[1] > start[1]) - (end[1] < start[1])))
    return tuple(edges)


@lru_cache(maxsize=8)
def _column_centres(grid: CellGrid) -> tuple[float, ...]:
    """Cache actual D9 centre doubles; bounded retention supports repeated immutable grids."""
    return tuple(grid.bounds.x_min + (column + 0.5) * grid.cell_width for column in range(grid.columns))


def polygon_spans(vertices: tuple[Point, ...], edges: tuple[ScanEdge, ...],
                  grid: CellGrid, row: int) -> list[tuple[int, int]]:
    """Sweep exact integer ray transitions, then union closed boundary intervals."""
    z = grid.centre(0, row)[1]
    xs = _column_centres(grid)
    transitions, boundaries = [], []
    for edge in edges:
        if not edge.low_z <= z <= edge.high_z:
            continue
        # Horizontal edges supply only their closed boundary interval.
        if edge.direction == 0:
            left, right = sorted((edge.start[0], edge.end[0]))
            boundaries.append((bisect_left(xs, left), bisect_right(xs, right)))
            continue
        transition = _transition(edge, z, xs)
        if z < edge.high_z:
            transitions.append(transition)
        # Equality includes inactive maximum-Z ends and repeated rounded centres.
        if transition < len(xs) and _side(edge, xs[transition], z) == 0:
            end = _transition(edge, z, xs, strict=True)
            boundaries.append((transition, end))
    # Each active edge contributes a right-ray crossing on columns below its transition.
    inside = sum(transition > 0 for transition in transitions) % 2
    cursor = 0
    spans = list(boundaries)
    for transition in sorted(value for value in transitions if value > 0):
        if inside and cursor < transition:
            spans.append((cursor, transition))
        inside = not inside
        cursor = transition
    return _union(spans)


def _side(edge: ScanEdge, x: float, z: float) -> int:
    """Return positive strictly left, zero on crossing, or negative right, exactly."""
    return _orientation(edge.start, edge.end, (x, z)) * edge.direction


def _transition(edge: ScanEdge, z: float, xs: tuple[float, ...], *, strict: bool = False) -> int:
    """Find first centre at the crossing or beyond; strict finds the first beyond.

    A nominal intersection only proposes a transition. Exact adjacent comparisons
    prove it, or bounded binary search decides with the same adaptive predicate.
    """
    x1, z1 = edge.start
    x2, z2 = edge.end
    nominal = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
    guess = bisect_left(xs, nominal)
    # Strict-left or left-and-equal forms a monotone prefix of the actual centres.
    def before(column: int) -> bool:
        """Identify centres before the requested inclusive or exclusive transition."""
        side = _side(edge, xs[column], z)
        return side > 0 or (strict and side == 0)
    if (guess == 0 or before(guess - 1)) and (guess == len(xs) or not before(guess)):
        return guess
    low, high = 0, len(xs)
    while low < high:
        middle = (low + high) // 2
        if before(middle):
            low = middle + 1
        else:
            high = middle
    return low


def _union(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping closed-coverage intervals with exclusive column ends."""
    result: list[tuple[int, int]] = []
    for start, end in sorted((start, end) for start, end in spans if start < end):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result
