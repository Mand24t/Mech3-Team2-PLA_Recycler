"""Known-curve, missing-data and overlay checks for polynomial diameter inputs."""
import unittest
from polynomial_boundaries import least_squares_curve, fit_deviation_boundaries
from filament_processing import measure_filament_windows


class PolynomialBoundaryTests(unittest.TestCase):
    def test_qr_recovers_known_polynomial_without_extra_dependencies(self):
        samples = [(y, 10+.1*y+.003*y*y-.00001*y**3) for y in range(240)]
        curve = least_squares_curve(samples, 3)
        for y, x in samples:
            self.assertAlmostEqual(curve(y), x, places=9)
        for order in (0, 1, 5, 8):
            curve = least_squares_curve([(y, 12+0*y) for y in range(240)], order)
            self.assertAlmostEqual(curve(120), 12, places=8)

    def test_outermost_red_pixels_used_even_with_other_features_inside_window(self):
        width, height = 90, 40
        mask = bytearray(width*height)
        for y in range(height):
            for start in (0, 30, 60):
                for x in (start+5, start+12, start+20):
                    mask[y*width+x] = 255
        tracks, statuses, counts = fit_deviation_boundaries(mask, width, height, ((0,30),(30,60),(60,90)), 3)
        self.assertEqual(counts, (40,40,40))
        self.assertAlmostEqual(tracks[0][10][0], 5)
        self.assertAlmostEqual(tracks[0][10][1], 20)

    def test_missing_rows_excluded_from_fit_no_extrapolation_beyond_observed_range(self):
        w, h = 40, 40
        mask = bytearray(w*h)
        for y in range(3, 37):
            if y == 20:
                continue
            mask[y*w+10] = mask[y*w+25] = 255
        tracks, _, counts = fit_deviation_boundaries(mask, w, h, ((0,40),), 2)
        self.assertEqual(counts, (33,))
        self.assertEqual(tracks[0][0], (None,None))
        self.assertAlmostEqual(tracks[0][20][1]-tracks[0][20][0], 15)
        self.assertEqual(tracks[0][-1], (None,None))

    def test_invalid_order_sparse_or_zero_width_do_not_produce_a_diameter(self):
        with self.assertRaises(ValueError):
            least_squares_curve([(0,1),(1,2)], 9)
        with self.assertRaises(ValueError):
            least_squares_curve([(0,1),(1,2)], 3)
        for mask in (bytes(40*20), bytes(255 if x == 10 else 0 for y in range(20) for x in range(40))):
            tracks, _, _ = fit_deviation_boundaries(mask, 40, 20, ((0,40),), 3)
            self.assertTrue(all(pair == (None,None) for pair in tracks[0]))

    def test_fitted_float_widths_yellow_curves_red_interior_and_unchanged_raw(self):
        w,h = 90,40
        reference = tuple([100.5])*(w*h)
        pixels = bytes(150 if any(a<=x<b for a,b in ((5,21),(35,51),(65,81))) else 100
                       for y in range(h) for x in range(w))
        original = bytes(pixels)
        result = measure_filament_windows(pixels,w,h,((0,30),(30,60),(60,90)),4,10,
                  transparent_filament_mode=True,transparent_background=reference,polynomial_order=3)
        self.assertEqual(pixels, original)
        self.assertEqual(result.curve_sample_rows,(40,40,40))
        for value in result.mean_widths_px:
            self.assertAlmostEqual(value, 15)
        for x in (5,20,35,50,65,80):
            self.assertEqual(result.processed_rgb[3*x:3*x+3], b"\xff\xff\x00")
        self.assertEqual(result.processed_rgb[30:33],b"\xff\x00\x00")
        self.assertEqual(result.processed_rgb[:3],bytes([100])*3)
        self.assertEqual(result.gradient_mask[5],255)


if __name__ == '__main__':
    unittest.main()
