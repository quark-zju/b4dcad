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


class PlacementTest(unittest.TestCase):
    def test_rotation_about_point(self):
        from b4dcad import square
        import numpy as np

        np.testing.assert_allclose(
            cube(1, 1, 1).move(x=2).rotate(z=90.0, origin=(2, 0, 0)).bounding_box(),
            (1, 0, 0, 2, 1, 1),
            atol=1e-12,
        )
        np.testing.assert_allclose(
            square(1, 1).move(x=2).rotate(90.0, origin=(2, 0)).bounds(),
            (1, 0, 2, 1),
            atol=1e-12,
        )

    def test_orientation_and_section(self):
        import numpy as np

        part = cube(2, 3, 4)
        moved = part.orient((1, 0, 0), origin=(10, 0, 0), x_axis=(0, 1, 0))
        np.testing.assert_allclose(moved.bounding_box(), (10, 0, 0, 14, 2, 3))
        self.assertAlmostEqual(part.section((1, 0, 0), (1, 0, 0)).area(), 12)
        self.assertAlmostEqual(part.section(origin=(0, 0, 2)).area(), 6)
        for call in (
            lambda: part.orient((0, 0, 0)),
            lambda: part.section((0, 0, 0)),
            lambda: part.section(x_axis=(0, 0, 1)),
        ):
            with self.assertRaises(ValueError):
                call()


class BatchBooleanTest(unittest.TestCase):
    def test_shapes_and_solids_with_empty_operands(self):
        from b4dcad import Shape, square, union, difference, intersection

        for base, empty, measure in (
            (cube(2, 2, 2), Solid(), lambda x: x.get_volume()),
            (square(2, 2), Shape(), lambda x: x.area()),
        ):
            self.assertIsInstance(union([empty, base]), type(base))
            self.assertAlmostEqual(
                measure(union(x for x in [empty, base])), measure(base)
            )
            self.assertTrue(difference(empty, base).is_empty())
            self.assertTrue(intersection(base, empty).is_empty())
            self.assertTrue(intersection([empty, base]).is_empty())
            self.assertAlmostEqual(measure(difference(base, [])), measure(base))
        with self.assertRaises(TypeError):
            union(cube(), square())
        self.assertIsInstance(union(), Solid)


class SolidToolsTest(unittest.TestCase):
    def test_measure_and_cleanup(self):
        part = cube(2, 3, 4)
        self.assertTrue(part.contains((1, 1, 1)))
        self.assertFalse(part.contains((3, 1, 1)))
        self.assertFalse(Solid().contains((0, 0, 0)))
        self.assertAlmostEqual(part.min_gap(part.move(x=4.5)), 2.5)
        self.assertAlmostEqual(part.project().area(), 6)
        self.assertAlmostEqual(part.refine(2).simplify().get_volume(), 24)
        tiny = cube(0.01, 0.01, 0.01).move(x=10)
        self.assertAlmostEqual((part + tiny).prune().get_volume(), 24)
        self.assertAlmostEqual((part + tiny).prune(largest_only=True).get_volume(), 24)
        self.assertTrue(tiny.prune().is_empty())
        smoothed = part.smooth()
        self.assertGreater(smoothed.get_volume(), 0)
        self.assertGreater(smoothed.num_tri(), part.num_tri())

    def test_minkowski(self):
        part = cube(2, 2, 2)
        tool = cube(1, 1, 1)
        self.assertAlmostEqual(part.minkowski_sum(tool).get_volume(), 27)
        self.assertAlmostEqual(part.minkowski_difference(tool).get_volume(), 1)


class LoftSweepMeshTest(unittest.TestCase):
    def test_loft_taper_and_varying_vertex_counts(self):
        from b4dcad import loft, square, circle

        for match in ("arc", "shortest"):
            part = loft(
                [square(2, 2, center=True), square(4, 4, center=True)],
                [0.0, 3.0],
                match=match,
            )
            self.assertAlmostEqual(part.get_volume(), 28)
        self.assertGreater(
            loft([square(2, 2, center=True), circle(r=2)], [0, 3]).get_volume(), 0
        )
        for heights in ([0, 0], [1, 0], [0, float("nan")]):
            with self.assertRaises(ValueError):
                loft([square(), square()], heights)
        with self.assertRaises(ValueError):
            loft(
                [square(4, 4, center=True) - square(2, 2, center=True), square()],
                [0, 3],
            )

    def test_sweep_holes_closed_path_and_invalid_paths(self):
        from b4dcad import sweep, square, circle

        shape = square(4, 4, center=True) - square(2, 2, center=True)
        self.assertAlmostEqual(sweep(shape, [(0, 0, 0), (0, 0, 3)]).get_volume(), 36)
        loop = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
        self.assertEqual(sweep(circle(r=0.5), loop, closed=True).genus(), 1)
        for path in (
            [],
            [(0, 0, 0)],
            [(0, 0, 0), (0, 0, 0)],
            [(0, 0, 0), (1, 0, 0), (0, 0, 0)],
        ):
            with self.assertRaises(ValueError):
                sweep(shape, path)
        with self.assertRaises(ValueError):
            sweep(shape, loop, twist=45, closed=True)
