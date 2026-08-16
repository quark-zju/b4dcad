import unittest

import numpy as np
from manifold3d import Manifold

from b4dcad import OverhangResult, Solid, cube
from b4dcad.fdmutil import (
    detect_overhangs,
    fix_horizontal_overhangs,
    trim_overhangs,
)


class DetectOverhangsTest(unittest.TestCase):
    def test_cube_detects_only_horizontal_underside(self):
        result = detect_overhangs(Manifold.cube((2, 3, 4)), angle=45)

        self.assertEqual(len(result.triangle_indices), 2)
        self.assertAlmostEqual(result.area, 6.0)
        np.testing.assert_allclose(result.angles, 90.0)
        np.testing.assert_allclose(result.normals[:, 2], -1.0)

    def test_angle_90_accepts_horizontal_underside(self):
        result = detect_overhangs(Manifold.cube(), angle=90)

        self.assertTrue(result.is_empty())

    def test_build_direction_can_be_changed(self):
        result = detect_overhangs(
            Manifold.cube((2, 3, 4)), angle=45, build_direction=(1, 0, 0)
        )

        self.assertEqual(len(result.triangle_indices), 2)
        self.assertAlmostEqual(result.area, 12.0)
        np.testing.assert_allclose(result.normals[:, 0], -1.0)

    def test_invalid_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            detect_overhangs(Manifold.cube(), angle=91)
        with self.assertRaises(ValueError):
            detect_overhangs(Manifold.cube(), build_direction=(0, 0, 0))


class TrimOverhangsTest(unittest.TestCase):
    def test_supported_cube_is_unchanged(self):
        cube = Manifold.cube((2, 3, 4))

        result = trim_overhangs(cube, angle=45)

        self.assertAlmostEqual(result.volume(), cube.volume())

    def test_t_shape_is_trimmed_toward_lower_support(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        result = trim_overhangs(t_shape, angle=45)

        self.assertAlmostEqual(t_shape.volume(), 32.0)
        self.assertAlmostEqual(result.volume(), 26.0, places=5)
        self.assertLessEqual(result.volume(), t_shape.volume())

    def test_larger_angle_preserves_more_material(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        strict = trim_overhangs(t_shape, angle=30)
        permissive = trim_overhangs(t_shape, angle=60)

        self.assertLess(strict.volume(), permissive.volume())
        self.assertLess(permissive.volume(), t_shape.volume())

    def test_two_anchors_may_be_cut_apart(self):
        left = Manifold.cube((1, 2, 5)).translate((-3, -1, 0))
        right = Manifold.cube((1, 2, 5)).translate((2, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        bridge = left + right + roof

        result = trim_overhangs(bridge, angle=45)

        self.assertAlmostEqual(result.volume(), 26.0, places=5)
        self.assertEqual(len(result.decompose()), 2)

    def test_non_rectangular_region_is_left_unchanged(self):
        stem = Manifold.cylinder(5, 1, circular_segments=32)
        roof = Manifold.cylinder(1, 3, circular_segments=32).translate((0, 0, 5))
        mushroom = stem + roof

        result = trim_overhangs(mushroom, angle=45)

        self.assertIs(result, mushroom)

    def test_angle_90_returns_original(self):
        cube = Manifold.cube()

        self.assertIs(trim_overhangs(cube, angle=90), cube)

    def test_layer_height_is_ignored_for_compatibility(self):
        cube = Manifold.cube()

        self.assertIs(trim_overhangs(cube, layer_height=0), cube)


class AddOverhangSupportsTest(unittest.TestCase):
    def test_single_anchor_adds_a_downward_wedge(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        result = fix_horizontal_overhangs(t_shape, angle=45, mode="add")

        self.assertAlmostEqual(result.volume(), 40.0, places=5)

    def test_two_anchors_add_two_half_span_wedges(self):
        left = Manifold.cube((1, 2, 5)).translate((-3, -1, 0))
        right = Manifold.cube((1, 2, 5)).translate((2, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        bridge = left + right + roof

        result = fix_horizontal_overhangs(bridge, angle=45, mode="add")

        self.assertAlmostEqual(result.volume(), 40.0, places=5)

    def test_stricter_angle_adds_more_material(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        strict = fix_horizontal_overhangs(t_shape, angle=30, mode="add")
        permissive = fix_horizontal_overhangs(t_shape, angle=60, mode="add")

        self.assertGreater(strict.volume(), permissive.volume())

    def test_direction_filter_limits_modified_regions(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        result = fix_horizontal_overhangs(
            t_shape, angle=45, mode="add", directions="<X"
        )

        self.assertAlmostEqual(result.volume(), 36.0, places=5)

    def test_invalid_mode_and_zero_add_angle_are_rejected(self):
        with self.assertRaises(ValueError):
            fix_horizontal_overhangs(Manifold.cube(), mode="replace")
        with self.assertRaises(ValueError):
            fix_horizontal_overhangs(Manifold.cube(), angle=0, mode="add")


class SolidFdmApiTest(unittest.TestCase):
    def test_detection_is_available_on_solid(self):
        result = cube(2, 3, 4).detect_overhangs(angle=45)

        self.assertIsInstance(result, OverhangResult)
        self.assertAlmostEqual(result.area, 6.0)

    def test_trimming_returns_a_solid(self):
        result = cube().trim_overhangs(angle=45, layer_height=0.5)

        self.assertIsInstance(result, Solid)
        self.assertAlmostEqual(result.manifold.volume(), 1.0)


if __name__ == "__main__":
    unittest.main()
