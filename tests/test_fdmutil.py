import unittest

import numpy as np
from manifold3d import Manifold

from b4dcad.fdmutil import detect_overhangs


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


if __name__ == "__main__":
    unittest.main()
