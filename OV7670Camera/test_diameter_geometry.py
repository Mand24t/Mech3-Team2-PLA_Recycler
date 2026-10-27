"""Known geometry, symmetric reflection, and scale validation."""
import unittest
from diameter_geometry import diameter_scales, mean_diameter


class DiameterGeometryTests(unittest.TestCase):
    def test_known_direct_and_reflected_magnifications(self):
        scales = diameter_scales(320, 4, 2.304, 5, 55, 105)
        for actual, expected in zip(scales, (0.18, 0.09, 0.18)):
            self.assertAlmostEqual(actual, expected)
        # A 1.8 mm cylinder projects to 20 direct pixels and 10 reflected pixels.
        self.assertAlmostEqual(mean_diameter((10, 20, 10), scales), 1.8)

    def test_both_45_degree_mirrors_have_apex_axial_depth(self):
        # Reflect (0,55) across z-x=105 and z+x=105 independently.
        for normal_x in (-1, 1):
            dot = 55 - 105
            reflected_x = -2 * dot * normal_x / 2
            reflected_z = 55 - 2 * dot / 2
            self.assertEqual(abs(reflected_x), 50)
            self.assertEqual(reflected_z, 105)

    def test_direct_view_can_be_reassigned_and_resolution_preserves_diameter(self):
        first = diameter_scales(320, 4, 2.304, 5, 55, 105, direct_view=0)
        second = diameter_scales(640, 4, 2.304, 5, 55, 105, direct_view=0)
        self.assertAlmostEqual(mean_diameter((20, 10, 10), first), 1.8)
        self.assertAlmostEqual(mean_diameter((40, 20, 20), second), 1.8)

    def test_invalid_geometry_and_missing_measurements_are_rejected(self):
        for offset, filament, mirrors in ((-1, 55, 105), (55, 55, 105), (5, 105, 55)):
            with self.subTest(offset=offset, filament=filament), self.assertRaises(ValueError):
                diameter_scales(320, 4, 2.304, offset, filament, mirrors)
        for value in (0, -1, float('nan'), float('inf')):
            with self.subTest(focal=value), self.assertRaises(ValueError):
                diameter_scales(320, value, 2.304, 5, 55, 105)
        with self.assertRaises(ValueError):
            mean_diameter((10, None, 10), (0.18, 0.09, 0.18))


if __name__ == '__main__':
    unittest.main()
