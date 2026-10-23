import unittest

from b4dcad import square
from b4dcad.contrib import extrude_edges


class ExtrudeEdgesTest(unittest.TestCase):
    def test_fractional_dimensions_and_holes(self):
        shape = square(10, 10, center=True) - square(3, 3, center=True)
        for style in ("round", "chamfer"):
            part = extrude_edges(shape, 5.5, 0.5, style=style, bottom=False)
            self.assertAlmostEqual(part.bounding_box()[2], 0)
            self.assertAlmostEqual(part.bounding_box()[5], 5.5)
            self.assertFalse(part.contains((0, 0, 2)))
            self.assertTrue(part.contains((4, 0, 2)))
            self.assertLess(part.get_volume(), shape.area() * 5.5)

    def test_invalid_radius(self):
        for radius in (0, -1, 3):
            with self.assertRaises(ValueError):
                extrude_edges(square(10, 10), 5, radius)
