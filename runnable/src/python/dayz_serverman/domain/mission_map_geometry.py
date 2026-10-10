"""Terrain bounds, polygon simplicity and area, and the D9 cell-centre sampling predicates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Sequence

from .mission_map_values import (
    BOUNDS_SOURCES, MAX_TERRAIN_EXTENT, MAX_TERRAIN_ORIGIN, MIN_CALIBRATION_DETERMINANT, MIN_POLYGON_AREA,
    MIN_VERTEX_SPACING, TERRAIN_ID, ObjectRuleError, exact_fields, finite_number, matching_text,
)


# A native point (X, Z) in metres
Point = tuple[float, float]
# Static error bound of the float orientation determinant (Shewchuk, ccwerrboundA, with epsilon 2^-53)
ORIENTATION_ERROR_BOUND = (3.0 + 16.0 * 2.0 ** -53) * 2.0 ** -53
# Below this product size, underflow can break the bound, so the exact test decides
UNDERFLOW_GUARD = 1e-200


@dataclass(frozen=True)
class Bounds:
    """Confirmed axis-aligned terrain rectangle in native metres (R03)."""

    x_min: float
    z_min: float
    width: float
    height: float

    @property
    def x_max(self) -> float:
        """Return the native X of the right edge."""
        return self.x_min + self.width

    @property
    def z_max(self) -> float:
        """Return the native Z of the top edge."""
        return self.z_min + self.height

    @property
    def max_radius(self) -> float:
        """Return the largest allowed circle radius: the larger extent."""
        return max(self.width, self.height)

    def contains(self, x: float, z: float) -> bool:
        """Return whether a point lies inside the closed rectangle."""
        return self.x_min <= x <= self.x_max and self.z_min <= z <= self.z_max


# Fields of the terrain object, of its bounds and of an image calibration
TERRAIN_FIELDS = frozenset(("terrain_id", "bounds", "bounds_source", "bounds_confirmed"))
BOUNDS_FIELDS = frozenset(("x_min", "z_min", "width", "height"))
CALIBRATION_FIELDS = frozenset("abcdef")


def parse_terrain(value: object) -> dict[str, Any]:
    """Return the terrain identity, its bounds in native metres, their source and confirmation."""
    raw = exact_fields(value, "terrain", TERRAIN_FIELDS)
    bounds = exact_fields(raw["bounds"], "bounds", BOUNDS_FIELDS)
    result = {name: finite_number(bounds[name], f"bounds.{name}") for name in ("x_min", "z_min", "width", "height")}
    # Positive extents up to 100 km, and an origin within 1,000 km
    if not all(0 < result[name] <= MAX_TERRAIN_EXTENT for name in ("width", "height")):
        raise ObjectRuleError(f"bounds width and height must be above 0 and at most {MAX_TERRAIN_EXTENT:g} m")
    if not all(abs(result[name]) <= MAX_TERRAIN_ORIGIN for name in ("x_min", "z_min")):
        raise ObjectRuleError(f"bounds x_min and z_min must be within ±{MAX_TERRAIN_ORIGIN:g} m")
    if raw["bounds_source"] not in BOUNDS_SOURCES:
        raise ObjectRuleError("bounds_source must be detected or manual")
    if not isinstance(raw["bounds_confirmed"], bool):
        raise ObjectRuleError("bounds_confirmed must be true or false")
    return {"terrain_id": matching_text(raw["terrain_id"], "terrain_id", TERRAIN_ID), "bounds": result,
            "bounds_source": raw["bounds_source"], "bounds_confirmed": raw["bounds_confirmed"]}


def parse_calibration(value: object) -> dict[str, float] | None:
    """Return the six numbers a to f that map image pixels to native metres, or None (R04)."""
    if value is None:
        return None
    raw = exact_fields(value, "calibration", CALIBRATION_FIELDS)
    result = {name: finite_number(raw[name], f"calibration.{name}") for name in "abcdef"}
    # X = a·px + b·py + c and Z = d·px + e·py + f must be invertible
    if abs(result["a"] * result["e"] - result["b"] * result["d"]) < MIN_CALIBRATION_DETERMINANT:
        raise ObjectRuleError("the calibration cannot be inverted")
    return result


def _orientation(a: Point, b: Point, c: Point) -> int:
    """Return the exact sign of the turn a→b→c: 1 left, -1 right, 0 collinear."""
    # Float determinant, trusted only when it clears the static error bound
    left = (a[0] - c[0]) * (b[1] - c[1])
    right = (a[1] - c[1]) * (b[0] - c[0])
    determinant, magnitude = left - right, abs(left) + abs(right)
    if magnitude > UNDERFLOW_GUARD and abs(determinant) > ORIENTATION_ERROR_BOUND * magnitude:
        return 1 if determinant > 0 else -1
    # Otherwise decide with exact rationals of the same doubles
    ax, az, bx, bz, cx, cz = (Fraction(value) for value in (*a, *b, *c))
    cross = (ax - cx) * (bz - cz) - (az - cz) * (bx - cx)
    return (cross > 0) - (cross < 0)


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    """Return whether p lies on the closed segment a–b."""
    return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
            and _orientation(a, b, p) == 0)


def _segments_meet(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Return whether the closed segments a–b and c–d share at least one point."""
    first, second = _orientation(a, b, c), _orientation(a, b, d)
    third, fourth = _orientation(c, d, a), _orientation(c, d, b)
    # A proper crossing has each segment's ends on both sides of the other
    if first * second < 0 and third * fourth < 0:
        return True
    # Otherwise they meet only where an end lies on the other segment
    return (_on_segment(a, b, c) or _on_segment(a, b, d)
            or _on_segment(c, d, a) or _on_segment(c, d, b))


def polygon_area(vertices: Sequence[Point]) -> Fraction:
    """Return the exact signed shoelace area of the closed outline."""
    exact = [(Fraction(x), Fraction(z)) for x, z in vertices]
    total = Fraction(0)
    for index, (x, z) in enumerate(exact):
        next_x, next_z = exact[(index + 1) % len(exact)]
        total += x * next_z - next_x * z
    return total / 2


def polygon_problem(vertices: Sequence[Point]) -> str | None:
    """Return why an open vertex list is not a simple polygon of at least 1 m², or None."""
    count = len(vertices)
    # Refuse two vertices closer than the minimum spacing; a sweep along X visits only near pairs
    by_x = sorted(vertices)
    for index, (x, z) in enumerate(by_x):
        for other_x, other_z in by_x[index + 1:]:
            if other_x - x >= MIN_VERTEX_SPACING:
                break
            if math.hypot(other_x - x, other_z - z) < MIN_VERTEX_SPACING:
                return f"two polygon vertices are closer than {MIN_VERTEX_SPACING} m"
    edges = [(vertices[index], vertices[(index + 1) % count]) for index in range(count)]
    # Neighbouring edges share only their common vertex: no fold back along the same line
    for index in range(count):
        before, corner, after = vertices[index - 1], vertices[index], vertices[(index + 1) % count]
        if _orientation(before, corner, after) == 0:
            # On one line, the edges overlap when both other ends lie on the same side of the corner
            ahead = [Fraction(after[axis]) - Fraction(corner[axis]) for axis in (0, 1)]
            behind = [Fraction(before[axis]) - Fraction(corner[axis]) for axis in (0, 1)]
            if ahead[0] * behind[0] + ahead[1] * behind[1] > 0:
                return "two neighbouring polygon edges overlap"
    # Edges that are not neighbours share no point; compare only pairs whose X ranges overlap
    order = sorted(range(count), key=lambda edge: min(edges[edge][0][0], edges[edge][1][0]))
    for position, first in enumerate(order):
        a, b = edges[first]
        right = max(a[0], b[0])
        for second in order[position + 1:]:
            c, d = edges[second]
            if min(c[0], d[0]) > right:
                break
            if (first - second) % count in (1, count - 1):
                continue
            if _segments_meet(a, b, c, d):
                return "the polygon outline crosses or touches itself"
    # Require a usable area
    if abs(polygon_area(vertices)) < MIN_POLYGON_AREA:
        return f"the polygon area is below {MIN_POLYGON_AREA} m²"
    return None


def circle_covers(centre_x: float, centre_z: float, radius: float, x: float, z: float) -> bool:
    """Return whether a point lies in the closed circle, in double precision and D9 operation order."""
    # Square by multiplication: IEEE multiplication is correctly rounded, the C library's pow is not everywhere
    dx, dz = x - centre_x, z - centre_z
    return dx * dx + dz * dz <= radius * radius


def polygon_covers(vertices: Sequence[Point], x: float, z: float) -> bool:
    """Return whether a point lies on the outline or inside by the even-odd rule, exactly."""
    point = (x, z)
    inside = False
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        # A point on an edge is inside
        if _on_segment(start, end, point):
            return True
        # Count edges that cross the horizontal ray to the right: the point is left of an upward edge
        if (start[1] > z) != (end[1] > z):
            if _orientation(start, end, point) == (1 if end[1] > start[1] else -1):
                inside = not inside
    return inside


@dataclass(frozen=True)
class CellGrid:
    """Native tier-cell grid of C columns along X and R rows along Z over the bounds (D9)."""

    bounds: Bounds
    columns: int
    rows: int

    def __post_init__(self) -> None:
        """Refuse an empty grid."""
        if self.columns < 1 or self.rows < 1:
            raise ValueError("a cell grid needs at least one column and one row")

    @property
    def cell_width(self) -> float:
        """Return the cell width sx = W / C in metres."""
        return self.bounds.width / self.columns

    @property
    def cell_height(self) -> float:
        """Return the cell height sz = H / R in metres."""
        return self.bounds.height / self.rows

    def centre(self, column: int, row: int) -> Point:
        """Return the native centre of cell (i, j); row 0 is at the lowest Z."""
        return (self.bounds.x_min + (column + 0.5) * self.cell_width,
                self.bounds.z_min + (row + 0.5) * self.cell_height)

    def cell_at(self, x: float, z: float) -> tuple[int, int]:
        """Return the cell of an Inspect click, limited to the grid."""
        column = math.floor((x - self.bounds.x_min) / self.cell_width)
        row = math.floor((z - self.bounds.z_min) / self.cell_height)
        return (min(max(column, 0), self.columns - 1), min(max(row, 0), self.rows - 1))


def tier_writes_possible(bounds: Bounds, confirmed: bool, world_width: float, world_height: float) -> bool:
    """Return whether tier cells may be sampled: confirmed bounds equal the header world at origin (0, 0)."""
    return (confirmed and bounds.x_min == 0 and bounds.z_min == 0
            and bounds.width == world_width and bounds.height == world_height)
