"""Evidence-based transparent-rim detection with known synthetic boundaries."""
import random
import unittest
from filament_processing import measure_filament_windows


WIDTH, HEIGHT = 180, 60
BOUNDS = ((0, 60), (60, 120), (120, 180))


def clear_scene(bright=False, slope=False, missing=(), noise=0, extra=False):
    rng = random.Random(43)
    pixels = bytearray()
    for y in range(HEIGHT):
        shift = y//15 if slope else 0
        for x in range(WIDTH):
            local = x % 60
            value = 130 + local//6  # slow illumination drift, not a dark body
            if y not in missing:
                if local in (18+shift, 42+shift) or extra and local == 30+shift:
                    value += 18 if bright else -18
            value += rng.randint(-noise, noise)
            pixels.append(value)
    return bytes(pixels)


def measure(pixels, **kwargs):
    return measure_filament_windows(pixels, WIDTH, HEIGHT, BOUNDS, 2, 10,
                                    transparent_filament_mode=True, **kwargs)


class TransparentFilamentTests(unittest.TestCase):
    def test_clear_interior_dark_or_bright_rims_produces_full_width(self):
        for bright in (False, True):
            pixels = clear_scene(bright=bright)
            result = measure(pixels)
            self.assertTrue(result.transparent_filament_mode)
            self.assertEqual(result.mean_widths_px, (24, 24, 24))
            self.assertEqual(result.valid_rows, (HEIGHT,)*3)
            self.assertEqual(result.left_boundary_x[20], (18, 78, 138))
            self.assertEqual(result.right_boundary_x[20], (42, 102, 162))
            self.assertEqual(result.centerline_x[20], (30, 90, 150))
            self.assertEqual(result.processed_rgb[:3], bytes([pixels[0]])*3)

    def test_sloping_filament_and_noise_preserve_width(self):
        result = measure(clear_scene(slope=True, noise=2))
        for mean, valid in zip(result.mean_widths_px, result.valid_rows):
            self.assertIsNotNone(mean)
            self.assertAlmostEqual(mean, 24, delta=0.6)
            self.assertGreater(valid, HEIGHT*0.8)
        self.assertGreater(result.centerline_x[-1][0], result.centerline_x[0][0])

    def test_inversion_does_not_change_selected_rims_or_source(self):
        pixels = clear_scene()
        normal = measure(pixels)
        inverted = measure(pixels, light_filament_mode=True)
        self.assertEqual(normal.widths_px, inverted.widths_px)
        self.assertEqual(inverted.processed_rgb[:3], bytes([255-pixels[0]])*3)
        self.assertEqual(pixels, clear_scene())

    def test_flat_shading_and_random_noise_do_not_create_diameters(self):
        rng = random.Random(8)
        cases = (bytes([130])*WIDTH*HEIGHT,
                 bytes(80+x%60 for y in range(HEIGHT) for x in range(WIDTH)),
                 bytes(rng.randint(110, 150) for _ in range(WIDTH*HEIGHT)))
        for pixels in cases:
            result = measure(pixels)
            self.assertEqual(result.mean_widths_px, (None,)*3)
            self.assertEqual(result.valid_rows, (0,)*3)

    def test_one_rim_or_clipped_window_is_not_a_filament(self):
        pixels = bytes(110 if x%60 == 18 else 130 for y in range(HEIGHT) for x in range(WIDTH))
        self.assertEqual(measure(pixels).mean_widths_px, (None,)*3)
        result = measure_filament_windows(clear_scene(), WIDTH, HEIGHT,
                 ((0, 35), BOUNDS[1], BOUNDS[2]), 2, 10, transparent_filament_mode=True)
        self.assertIsNone(result.mean_widths_px[0])
        self.assertEqual(result.mean_widths_px[1:], (24, 24))

    def test_similarly_strong_alternative_rims_are_rejected(self):
        result = measure(clear_scene(extra=True))
        self.assertEqual(result.mean_widths_px, (None,)*3)
        self.assertTrue(all('ambiguous' in reason for reason in result.transparent_status))

    def test_missing_rows_are_not_filled_with_guessed_widths(self):
        pixels = clear_scene(missing=range(25, 28))
        result = measure_filament_windows(pixels, WIDTH, HEIGHT, BOUNDS, 2, 1,
                                         transparent_filament_mode=True)
        self.assertEqual(result.widths_px[26], (None,)*3)
        self.assertEqual(result.valid_rows, (57,)*3)
        self.assertEqual(result.mean_widths_px, (24,)*3)

    def test_insufficient_support_and_unreasonable_threshold_reject(self):
        result = measure(clear_scene(missing=range(0, 40)))
        self.assertEqual(result.mean_widths_px, (None,)*3)
        result = measure_filament_windows(clear_scene(), WIDTH, HEIGHT, BOUNDS, 255, 10,
                                         transparent_filament_mode=True)
        self.assertEqual(result.mean_widths_px, (None,)*3)
        with self.assertRaises(ValueError):
            measure_filament_windows(clear_scene(), WIDTH, HEIGHT, BOUNDS, transparent_filament_mode=1)


class TransparentBackgroundTests(unittest.TestCase):
    bounds = ((5, 55), (65, 115), (125, 175))

    def reference(self):
        # Fixed clutter and shading, including a strong line inside each ROI.
        return bytes(100+x//4+(35 if x%60 == 30 else 0)
                     for y in range(HEIGHT) for x in range(WIDTH))

    def scene(self, reference, gain=1, offset=0, missing=()):
        return bytes(round(reference[y*WIDTH+x]*gain+offset+
                          (35 if 18 <= x%60 < 42 and y not in missing else 0))
                     for y in range(HEIGHT) for x in range(WIDTH))

    def detect(self, pixels, reference):
        return measure_filament_windows(pixels, WIDTH, HEIGHT, self.bounds, 4, 1,
                 transparent_filament_mode=True, transparent_background=reference,
                 require_transparent_background=True)

    def test_reference_removes_fixed_clutter_and_measures_bright_stripe(self):
        reference = self.reference()
        result = self.detect(self.scene(reference), reference)
        self.assertEqual(result.mean_widths_px, (24,)*3)
        self.assertEqual(result.left_boundary_x[20], (18, 78, 138))
        self.assertEqual(result.right_boundary_x[20], (42, 102, 162))
        self.assertEqual(result.valid_rows, (HEIGHT,)*3)
        self.assertEqual(self.detect(reference, reference).mean_widths_px, (None,)*3)

    def test_literal_threshold_highlights_exposure_changes_instead_of_compensating(self):
        reference = self.reference()
        offset_only = bytes(value+7 for value in reference)
        result = self.detect(offset_only, reference)
        self.assertEqual(result.gradient_mask, bytes([255])*WIDTH*HEIGHT)
        self.assertEqual(result.processed_rgb, b"\xff\x00\x00"*WIDTH*HEIGHT)

    def test_all_deviating_pixels_are_red_including_outside_windows_and_fractional_reference(self):
        reference = tuple([100.5])*WIDTH*HEIGHT
        pixels = bytearray([100])*WIDTH*HEIGHT
        for x, value in ((0, 106), (2, 96), (3, 105)):
            pixels[x] = value
        result = self.detect(bytes(pixels), reference)
        # Threshold=4; abs(105-100.5)=4.5 also exceeds it. No rounding.
        for x in (0, 2, 3):
            self.assertEqual(result.processed_rgb[x*3:x*3+3], b"\xff\x00\x00")
        self.assertEqual(result.processed_rgb[3:6], bytes([100])*3)
        boundary = measure_filament_windows(bytes([104])*WIDTH*HEIGHT, WIDTH, HEIGHT,
                     self.bounds, 4, 1, transparent_filament_mode=True,
                     transparent_background=bytes([100])*WIDTH*HEIGHT)
        self.assertEqual(boundary.gradient_mask, bytes(WIDTH*HEIGHT))  # equality is not exceeding

    def test_missing_foreground_stays_missing_and_not_a_previous_width(self):
        reference = self.reference()
        result = self.detect(self.scene(reference, missing=range(24, 30)), reference)
        self.assertEqual(result.widths_px[25], (None,)*3)
        self.assertEqual(result.valid_rows, (54,)*3)

    def test_adjacent_dark_shadow_and_scattered_reflections_do_not_expand_width(self):
        reference = self.reference()
        pixels = bytearray(self.scene(reference))
        for y in range(HEIGHT):
            for region in range(3):
                offset = y*WIDTH+60*region
                for x in range(10, 18):  # touches the strand but has opposite polarity
                    pixels[offset+x] = reference[offset+x]-25
                if y%11 == 0:
                    for x in range(46, 50):
                        pixels[offset+x] = reference[offset+x]+45
        result = self.detect(bytes(pixels), reference)
        self.assertEqual(result.mean_widths_px, (24,)*3)
        self.assertEqual(result.valid_rows, (HEIGHT,)*3)
        self.assertTrue(all(row == (18, 78, 138) for row in result.left_boundary_x))

    def test_each_view_can_have_a_different_width_and_follow_slope(self):
        reference = self.reference()
        pixels = bytearray(reference)
        for y in range(HEIGHT):
            shift = y//15
            for region, span in enumerate((8, 12, 16)):
                for x in range(20+shift, 20+shift+span):
                    index = y*WIDTH+region*60+x
                    pixels[index] += 45
        result = self.detect(bytes(pixels), reference)
        self.assertEqual(result.mean_widths_px, (8, 12, 16))
        self.assertEqual(result.centerline_x[-1][0]-result.centerline_x[0][0], 3)

    def test_weak_bottom_rows_are_not_filled_and_do_not_erase_supported_upper_rows(self):
        reference = self.reference()
        result = self.detect(self.scene(reference, missing=range(50, 60)), reference)
        self.assertEqual(result.valid_rows, (50,)*3)
        self.assertEqual(result.widths_px[-1], (None,)*3)
        self.assertEqual(result.mean_widths_px, (24,)*3)

    def test_competing_bright_bands_are_rejected_rather_than_combined(self):
        reference = bytes([100])*WIDTH*HEIGHT
        pixels = bytes(150 if 15 <= x%60 < 21 or 35 <= x%60 < 41 else 100
                       for y in range(HEIGHT) for x in range(WIDTH))
        result = self.detect(pixels, reference)
        self.assertEqual(result.mean_widths_px, (None,)*3)
        self.assertTrue(all("multiple bright stripes" in reason for reason in result.transparent_status))

    def test_no_reference_in_gui_mode_never_generates_an_estimate(self):
        result = measure_filament_windows(clear_scene(), WIDTH, HEIGHT, BOUNDS, 2, 10,
                 transparent_filament_mode=True, require_transparent_background=True)
        self.assertEqual(result.mean_widths_px, (None,)*3)
        self.assertTrue(all("Capture Background" in text for text in result.transparent_status))
        with self.assertRaises(ValueError):
            self.detect(clear_scene(), bytes(10))

    def test_random_sensor_noise_and_scene_wide_change_rejected(self):
        reference = self.reference()
        rng = random.Random(21)
        noisy = bytes(value+rng.randint(-3, 3) for value in reference)
        self.assertEqual(self.detect(noisy, reference).mean_widths_px, (None,)*3)
        changed = bytes(min(255, round(value*1.5)) for value in reference)
        result = self.detect(changed, reference)
        self.assertEqual(result.mean_widths_px, (None,)*3)
        self.assertTrue(all(value == 255 for value in result.gradient_mask))


if __name__ == '__main__':
    unittest.main()
