import unittest

import numpy as np
from manifold3d import Manifold

from b4dcad.fdmutil import detect_overhangs, trim_overhangs


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

        result = trim_overhangs(cube, angle=45, layer_height=1)

        self.assertAlmostEqual(result.volume(), cube.volume())

    def test_t_shape_is_trimmed_toward_lower_support(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        result = trim_overhangs(t_shape, angle=45, layer_height=1)

        self.assertAlmostEqual(t_shape.volume(), 32.0)
        self.assertAlmostEqual(result.volume(), 28.0, places=5)
        self.assertLessEqual(result.volume(), t_shape.volume())

    def test_larger_angle_preserves_more_material(self):
        stem = Manifold.cube((2, 2, 5)).translate((-1, -1, 0))
        roof = Manifold.cube((6, 2, 1)).translate((-3, -1, 5))
        t_shape = stem + roof

        strict = trim_overhangs(t_shape, angle=30, layer_height=1)
        permissive = trim_overhangs(t_shape, angle=60, layer_height=1)

        self.assertLess(strict.volume(), permissive.volume())
        self.assertLess(permissive.volume(), t_shape.volume())

    def test_angle_90_returns_original(self):
        cube = Manifold.cube()

        self.assertIs(trim_overhangs(cube, angle=90), cube)

    def test_invalid_layer_height_is_rejected(self):
        with self.assertRaises(ValueError):
            trim_overhangs(Manifold.cube(), layer_height=0)


if __name__ == "__main__":
    unittest.main()
