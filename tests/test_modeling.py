import unittest

from b4dcad import Solid, cube, hull_points


class CompatibilityTest(unittest.TestCase):
    def test_measurements(self):
        part = cube(2.5, 3, 4)
        self.assertEqual(part.genus(), 0)
        self.assertAlmostEqual(part.get_volume(), 30)
        self.assertAlmostEqual(part.get_surface_area(), 59)
        self.assertGreater(part.precision(), 0)

    def test_split_accepts_wrapper_and_native(self):
        part = cube(2, 2, 2)
        cutter = cube(1, 2, 2)
        for other in (cutter, cutter.manifold):
            inside, outside = part.split(other)
            self.assertAlmostEqual(inside.get_volume(), 4)
            self.assertAlmostEqual(outside.get_volume(), 4)

    def test_hull_points_is_solid(self):
        part = hull_points([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])
        self.assertIsInstance(part, Solid)
        self.assertAlmostEqual(part.get_volume(), 1 / 6)
