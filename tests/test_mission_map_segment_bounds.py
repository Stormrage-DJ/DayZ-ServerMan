"""Compare closed segment intersection against exact rational geometry."""
from __future__ import annotations

import itertools
import math
import random
import unittest
from fractions import Fraction

from dayz_serverman.domain.mission_map_geometry import _segments_meet


def exact_meeting(a, b, c, d):
    """Decide segment intersection from exact rational coordinates."""
    a, b, c, d = [tuple(map(Fraction, point)) for point in (a, b, c, d)]

    def turn(p, q, r):
        determinant = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (determinant > 0) - (determinant < 0)

    def contains(p, q, r):
        return (turn(p, q, r) == 0 and min(p[0], q[0]) <= r[0] <= max(p[0], q[0])
                and min(p[1], q[1]) <= r[1] <= max(p[1], q[1]))

    return (turn(a, b, c) * turn(a, b, d) < 0 and turn(c, d, a) * turn(c, d, b) < 0
            or contains(a, b, c) or contains(a, b, d) or contains(c, d, a) or contains(c, d, b))


class SegmentBoundsTests(unittest.TestCase):
    """Exercise equality, disjoint bounds, exact fallback and segment symmetry."""

    def test_boundary_classes_and_reversed_endpoints(self):
        """Touches and overlaps remain closed, including nextafter neighbours."""
        next_one = math.nextafter(1.0, math.inf)
        cases = [
            ((0., 0.), (2., 2.), (0., 2.), (2., 0.)),
            ((0., 0.), (1., 1.), (1., 1.), (2., 0.)),
            ((0., 0.), (2., 0.), (1., 0.), (3., 0.)),
            ((0., 0.), (1., 0.), (next_one, 0.), (2., 0.)),
            ((1., 0.), (1., 2.), (0., 1.), (2., 1.)),
            ((1., 0.), (1., 1.), (1., next_one), (1., 2.)),
            ((0., 0.), (0., 0.), (0., 0.), (1., 1.)),
            ((0., 0.), (2., 0.), (1., next_one), (1., 2.)),
            ((0., 0.), (1e-110, 1e-110), (0., 1e-110), (1e-110, 0.)),
            ((1e6, 1e6), (1e6 + 1., 1e6 + 1.),
             (1e6, 1e6 + 1.), (1e6 + 1., math.nextafter(1e6, math.inf))),
        ]
        for a, b, c, d in cases:
            expected = exact_meeting(a, b, c, d)
            for first, second in itertools.product(((a, b), (b, a)), ((c, d), (d, c))):
                with self.subTest(points=(*first, *second)):
                    self.assertEqual(_segments_meet(*first, *second), expected)
                    self.assertEqual(_segments_meet(*second, *first), expected)

    def test_seeded_integer_and_near_collinear_segments(self):
        """Independent exact decisions match diverse intersecting and disjoint pairs."""
        random_source = random.Random(20261011)
        for index in range(3000):
            points = [tuple(float(random_source.randint(-16, 16)) for _ in range(2)) for _ in range(4)]
            if index % 3 == 0:
                points = [(x, math.nextafter(x, math.inf) if index % 2 else x) for x, _ in points]
            with self.subTest(index=index):
                self.assertEqual(_segments_meet(*points), exact_meeting(*points))
