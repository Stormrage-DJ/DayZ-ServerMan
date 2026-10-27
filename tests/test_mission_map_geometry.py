"""Cover terrain bounds, polygon simplicity and area, and the D9 cell-centre sampling predicates."""

from __future__ import annotations

import math
import time
import unittest
from fractions import Fraction

from dayz_serverman.domain.mission_map_geometry import (
    Bounds, CellGrid, circle_covers, parse_calibration, parse_terrain, polygon_area, polygon_covers, polygon_problem,
    tier_writes_possible,
)
from dayz_serverman.domain.mission_map_values import ObjectRuleError


# A concave U shape: the notch between X 4 and 6 reaches down to Z 2
U_SHAPE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (6.0, 10.0), (6.0, 2.0), (4.0, 2.0), (4.0, 10.0), (0.0, 10.0)]


class BoundsTests(unittest.TestCase):
    """Verify the closed terrain rectangle."""

    def test_closed_rectangle_and_largest_radius(self) -> None:
        """Edges and corners are inside; the largest radius is the larger extent."""
        bounds = Bounds(-100.0, 50.0, 200.0, 300.0)
        self.assertEqual((bounds.x_max, bounds.z_max, bounds.max_radius), (100.0, 350.0, 300.0))
        for point in ((-100.0, 50.0), (100.0, 350.0), (0.0, 50.0), (100.0, 200.0)):
            self.assertTrue(bounds.contains(*point), point)
        for point in ((-100.000001, 50.0), (0.0, 350.0001), (101.0, 0.0)):
            self.assertFalse(bounds.contains(*point), point)

    def test_terrain_rules(self) -> None:
        """Extents above 0 and up to 100 km, origins within 1,000 km, a known source and a terrain identity."""
        base = {"terrain_id": "chernarusplus", "bounds": {"x_min": 0.0, "z_min": 0.0, "width": 15360.0,
                "height": 15360.0}, "bounds_source": "detected", "bounds_confirmed": False}
        self.assertEqual(parse_terrain(base), base)
        bad_bounds = [("width", 0.0), ("height", -5.0), ("width", 100_000.5), ("x_min", 1_000_000.5),
                      ("z_min", float("inf")), ("x_min", "0"), ("height", True)]
        for field, value in bad_bounds:
            raw = {**base, "bounds": {**base["bounds"], field: value}}
            with self.subTest(field=field, value=value), self.assertRaises(ObjectRuleError):
                parse_terrain(raw)
        for field, value in (("bounds_source", "guessed"), ("bounds_confirmed", 1), ("terrain_id", "bad id")):
            with self.subTest(field=field), self.assertRaises(ObjectRuleError):
                parse_terrain({**base, field: value})
        # The largest extent and origin are inside the limits; integers become floats
        edge = {**base, "bounds": {"x_min": -1_000_000, "z_min": 1_000_000, "width": 100_000, "height": 1}}
        self.assertEqual(parse_terrain(edge)["bounds"]["width"], 100_000.0)

    def test_calibration_must_be_invertible(self) -> None:
        """Six finite numbers a to f whose determinant a·e − b·d is at least 1e-12 in absolute value."""
        self.assertIsNone(parse_calibration(None))
        flipped = {"a": 2.0, "b": 0.0, "c": 0.0, "d": 0.0, "e": -2.0, "f": 4096.0}
        self.assertEqual(parse_calibration(flipped), flipped)
        for bad in ({**flipped, "a": 1, "b": 2, "d": 2, "e": 4}, {**flipped, "g": 0.0}, {**flipped, "c": None}):
            with self.subTest(bad=bad), self.assertRaises(ObjectRuleError):
                parse_calibration(bad)


class PolygonRuleTests(unittest.TestCase):
    """Verify the simple-outline rules of D1 with exact orientation tests."""

    def test_simple_shapes_pass(self) -> None:
        """A square, a concave U shape and a triangle are simple."""
        square = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        for vertices in (square, U_SHAPE, [(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]):
            self.assertIsNone(polygon_problem(vertices), vertices)

    def test_self_intersection_is_refused(self) -> None:
        """A bow tie crosses itself."""
        bow_tie = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]
        self.assertIn("crosses or touches", polygon_problem(bow_tie))

    def test_touching_non_neighbour_edges_are_refused(self) -> None:
        """A vertex on a non-neighbouring edge, and a repeated position along the outline, are refused."""
        vertex_on_edge = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (5.0, 0.0), (0.0, 10.0)]
        self.assertIn("crosses or touches", polygon_problem(vertex_on_edge))
        # Two non-neighbour edges that overlap along one line
        overlap = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (2.0, 5.0), (2.0, 0.0), (8.0, 0.0), (8.0, -5.0),
                   (0.0, -5.0)]
        self.assertIsNotNone(polygon_problem(overlap))

    def test_neighbour_fold_back_is_refused(self) -> None:
        """An outline that folds back along the same line overlaps its neighbour edge."""
        fold = [(0.0, 0.0), (10.0, 0.0), (5.0, 0.0), (5.0, 5.0)]
        self.assertIn("neighbouring", polygon_problem(fold))
        # A straight run through a vertex is not a fold
        self.assertIsNone(polygon_problem([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]))

    def test_vertex_spacing_and_area(self) -> None:
        """Vertices closer than 0.001 m and an area below 1 m² are refused."""
        close = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (10.0, 10.0009), (0.0, 10.0)]
        self.assertIn("closer than", polygon_problem(close))
        self.assertIsNone(polygon_problem([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (10.001, 10.001), (0.0, 10.0)]))
        thin = [(0.0, 0.0), (10.0, 0.0), (10.0, 0.09)]
        self.assertIn("area", polygon_problem(thin))
        # A collinear triangle has no area and folds back
        self.assertIsNotNone(polygon_problem([(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]))

    def test_area_is_exact_and_signed(self) -> None:
        """Counter-clockwise outlines have positive area; the value is exact."""
        self.assertEqual(polygon_area([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]), Fraction(1, 2))
        self.assertEqual(polygon_area([(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)]), Fraction(-1, 2))
        self.assertEqual(polygon_area([(0.1, 0.0), (0.3, 0.0), (0.3, 0.1)]),
                         (Fraction(0.3) - Fraction(0.1)) * Fraction(0.1) / 2)

    def test_exact_orientation_decides_near_collinear_points(self) -> None:
        """A spike back to a point exactly on the line folds; the same spike one ulp off the line does not."""
        apex = (0.0, 50.0)
        # (0.1, 0.1), (0.3, 0.3) and (0.2, 0.2) lie exactly on X = Z, although 0.1 + 0.2 != 0.3 in floats
        self.assertIn("neighbouring", polygon_problem([(0.1, 0.1), (0.3, 0.3), (0.2, 0.2), apex]))
        beside = (0.2, math.nextafter(0.2, 1.0))
        self.assertIsNone(polygon_problem([(0.1, 0.1), (0.3, 0.3), beside, apex]))

    def test_largest_polygon_validates_quickly(self) -> None:
        """A 512-vertex outline validates well inside one second."""
        ring = [(500 + 400 * math.cos(2 * math.pi * index / 512), 500 + 400 * math.sin(2 * math.pi * index / 512))
                for index in range(512)]
        started = time.perf_counter()
        self.assertIsNone(polygon_problem(ring))
        self.assertLess(time.perf_counter() - started, 1.0)


class SamplingTests(unittest.TestCase):
    """Verify the D9 coverage predicates and the cell grid."""

    def test_circle_boundary_is_inside(self) -> None:
        """A point at exactly the radius is covered."""
        self.assertTrue(circle_covers(0.0, 0.0, 5.0, 3.0, 4.0))
        self.assertFalse(circle_covers(0.0, 0.0, 5.0, 3.0, 4.000001))

    def test_circle_squares_by_multiplication(self) -> None:
        """A22 boundary cases: the D9 double-precision test squares by correctly rounded multiplication."""
        # (centre X, centre Z, radius, X, Z) in hexadecimal; the C library's pow gave the opposite answer here
        cases = [
            ("0x1.19ed347fb6a97p+12", "0x1.256861ec7423fp+14", "0x1.f372194789a0ap+10", "0x1.4d95c3b396f36p+11",
             "0x1.317ca99bb39c2p+14", False),
            ("0x1.2c62edf6cb394p+11", "0x1.5a5b929e41ea0p+8", "0x1.7c773f80964f0p+10", "0x1.6101aba3d1afap+11",
             "0x1.c435e0ee4178dp+10", True),
            ("0x1.3b16e956f1ddep+12", "0x1.6cf9a3fbda94cp+10", "0x1.5f6dfb9796662p+10", "0x1.7f291773264a1p+12",
             "0x1.2593ec01240cfp+11", True),
        ]
        for *values, expected in cases:
            centre_x, centre_z, radius, x, z = (float.fromhex(value) for value in values)
            self.assertIs(circle_covers(centre_x, centre_z, radius, x, z), expected, values)

    def test_polygon_edges_vertices_and_even_odd(self) -> None:
        """Points on edges and vertices are inside; the U notch is outside by the even-odd rule."""
        for point in ((0.0, 5.0), (5.0, 2.0), (4.0, 6.0), (10.0, 10.0), (6.0, 2.0), (1.0, 1.0), (8.0, 9.0)):
            self.assertTrue(polygon_covers(U_SHAPE, *point), point)
        for point in ((5.0, 5.0), (5.0, 9.99), (-0.001, 5.0), (11.0, 5.0), (5.0, 10.0)):
            self.assertFalse(polygon_covers(U_SHAPE, *point), point)
        # A sloped edge: a point exactly on it is inside, one ulp outside is not
        triangle = [(0.0, 0.0), (4.0, 0.0), (0.0, 2.0)]
        self.assertTrue(polygon_covers(triangle, 2.0, 1.0))
        self.assertFalse(polygon_covers(triangle, 2.0, math.nextafter(1.0, 2.0)))

    def test_cell_centres_and_inspect_cells(self) -> None:
        """Cell (i, j) centres use sx and sz; row 0 is at the lowest Z; Inspect clicks are clamped."""
        grid = CellGrid(Bounds(0.0, 0.0, 20480.0, 20480.0), 4096, 4096)
        self.assertEqual((grid.cell_width, grid.cell_height), (5.0, 5.0))
        self.assertEqual(grid.centre(0, 0), (2.5, 2.5))
        self.assertEqual(grid.centre(4095, 1), (20477.5, 7.5))
        self.assertEqual(grid.cell_at(10240.0, 10240.0), (2048, 2048))
        self.assertEqual(grid.cell_at(4.999, 5.0), (0, 1))
        self.assertEqual(grid.cell_at(-50.0, 20480.0), (0, 4095))
        offset = CellGrid(Bounds(-10.0, 100.0, 30.0, 20.0), 3, 2)
        self.assertEqual(offset.centre(2, 1), (15.0, 115.0))
        self.assertRaises(ValueError, CellGrid, Bounds(0.0, 0.0, 1.0, 1.0), 0, 1)

    def test_tier_writes_need_confirmed_header_bounds_at_origin(self) -> None:
        """Tier cells are sampled only for confirmed bounds equal to the header world at (0, 0)."""
        bounds = Bounds(0.0, 0.0, 15360.0, 15360.0)
        self.assertTrue(tier_writes_possible(bounds, True, 15360.0, 15360.0))
        self.assertFalse(tier_writes_possible(bounds, False, 15360.0, 15360.0))
        self.assertFalse(tier_writes_possible(bounds, True, 12800.0, 12800.0))
        self.assertFalse(tier_writes_possible(Bounds(1.0, 0.0, 15360.0, 15360.0), True, 15360.0, 15360.0))


if __name__ == "__main__":
    unittest.main()
