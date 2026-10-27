"""Ground-truth checks for three-window gradient-based pixel measurements."""
import unittest
from filament_processing import measure_filaments, vertical_average


def scene(rows):
    pixels = bytearray()
    for widths in rows:
        row = bytearray([220] * 90)
        if widths is not None:
            for center, width in zip((15, 45, 75), widths):
                start = center - width // 2
                row[start:start + width] = bytes([0]) * width
        pixels.extend(row)
    return bytes(pixels)


class FilamentProcessingTests(unittest.TestCase):
    def test_known_rectangles_return_boundary_distances_for_each_row(self):
        pixels = scene([(10, 8, 12)] * 4)
        result = measure_filaments(pixels, 90, 4, 0, 30, 60, 90, 20)
        self.assertEqual(result.center_x, (15, 45, 75))
        self.assertEqual(result.left_boundary_x, ((10, 41, 69),) * 4)
        self.assertEqual(result.right_boundary_x, ((20, 49, 81),) * 4)
        self.assertEqual(result.widths_px, ((10, 8, 12),) * 4)
        self.assertEqual(result.mean_widths_px, (10, 8, 12))
        self.assertEqual(result.valid_rows, (4, 4, 4))
        self.assertEqual(result.centerline_x, ((15, 45, 75),) * 4)
        self.assertEqual(set(result.filament_mask), {0, 255})
        self.assertEqual(sum(value == 255 for value in result.filament_mask), 120)

    def test_each_valid_row_contains_exactly_nine_single_pixel_red_marks(self):
        result = measure_filaments(scene([(10, 8, 12)] * 3), 90, 3, 0, 30, 60, 90)
        for y in range(3):
            red = [x for x in range(90)
                   if result.processed_rgb[(y * 90 + x) * 3:(y * 90 + x) * 3 + 3] == b"\xff\x00\x00"]
            self.assertEqual(red, [10, 15, 20, 41, 45, 49, 69, 75, 81])
        self.assertTrue(result.ppm().startswith(b"P6\n90 3\n255\n"))

    def test_outer_edges_enclose_internal_gradient_changes(self):
        row = bytearray([200] * 90)
        for base in (0, 30, 60):
            row[base + 8:base + 22] = bytes([60]) * 14
            row[base + 12:base + 18] = bytes([0]) * 6
        result = measure_filaments(bytes(row), 90, 1, 0, 30, 60, 90, 20)
        self.assertEqual(result.widths_px, ((14, 14, 14),))
        outer = measure_filaments(bytes(row), 90, 1, 0, 30, 60, 90, 80)
        self.assertEqual(outer.widths_px, ((14, 14, 14),))

    def test_threshold_comparison_is_strict_and_255_rejects_all_edges(self):
        pixels = bytes(10 if value == 0 else 30 for value in scene([(10, 8, 12)]))
        equal = measure_filaments(pixels, 90, 1, 0, 30, 60, 90, 20)
        below = measure_filaments(pixels, 90, 1, 0, 30, 60, 90, 19)
        self.assertEqual(equal.widths_px, ((None, None, None),))
        self.assertEqual(below.widths_px, ((10, 8, 12),))
        maximum = measure_filaments(scene([(10, 8, 12)]), 90, 1, 0, 30, 60, 90, 255)
        self.assertEqual(maximum.valid_rows, (0, 0, 0))

    def test_missing_edges_are_none_and_excluded_from_means(self):
        result = measure_filaments(scene([(10, 8, 12), None, (14, 12, 16)]), 90, 3, 0, 30, 60, 90, gradient_window_size=1)
        self.assertEqual(result.widths_px[1], (None, None, None))
        self.assertEqual(result.mean_widths_px, (12, 10, 14))
        self.assertEqual(result.valid_rows, (2, 2, 2))

    def test_region_dividers_cannot_be_mistaken_for_filament_edges(self):
        pixels = (bytes([0]) * 30 + bytes([100]) * 30 + bytes([200]) * 30) * 2
        result = measure_filaments(pixels, 90, 2, 0, 30, 60, 90, 0)
        self.assertEqual(result.widths_px, ((None, None, None),) * 2)
        self.assertEqual(result.mean_widths_px, (None, None, None))

    def test_internal_dark_minimum_does_not_anchor_the_centerline(self):
        row = bytearray([220] * 90)
        for base in (0, 30, 60):
            row[base + 7:base + 23] = bytes([80]) * 16
            row[base + 8:base + 10] = bytes([0]) * 2
        result = measure_filaments(bytes(row), 90, 1, 0, 30, 60, 90)
        self.assertEqual(result.center_x, (15, 45, 75))
        self.assertEqual(result.centerline_x, ((15, 45, 75),))
        self.assertEqual(result.widths_px, ((16, 16, 16),))

    def test_tiny_regions_remain_invalid_instead_of_borrowing_neighbor_edges(self):
        result = measure_filaments(scene([(10, 8, 12)]), 90, 1, 0, 1, 2, 90)
        self.assertEqual(result.valid_rows[:2], (0, 0))
        self.assertEqual(result.widths_px[0][:2], (None, None))

    def test_original_pixels_and_unmarked_background_are_preserved(self):
        pixels = scene([(10, 8, 12)])
        original = bytes(pixels)
        result = measure_filaments(pixels, 90, 1, 0, 30, 60, 90)
        self.assertEqual(pixels, original)
        self.assertEqual(result.processed_rgb[:3], bytes([220, 220, 220]))
        self.assertEqual(result.processed_rgb[11 * 3:12 * 3], bytes([0, 0, 0]))

    def test_black_borders_outside_outer_limits_are_ignored(self):
        row = bytearray(30 if value == 0 else 220 for value in scene([(10, 8, 12)]))
        row[:6] = bytes([0]) * 6
        row[85:] = bytes([0]) * 5
        result = measure_filaments(bytes(row) * 3, 90, 3, 6, 30, 60, 85)
        self.assertEqual(result.window_bounds, ((6, 30), (30, 60), (60, 85)))
        self.assertEqual(result.center_x, (15, 45, 75))
        self.assertEqual(result.mean_widths_px, (10, 8, 12))
        for y in range(3):
            for x in list(range(6)) + list(range(85, 90)):
                offset = (y * 90 + x) * 3
                self.assertEqual(result.processed_rgb[offset:offset + 3], bytes([0, 0, 0]))

    def test_cropped_edges_cannot_be_found_outside_the_new_limits(self):
        result = measure_filaments(scene([(10, 8, 12)]) * 3, 90, 3, 10, 30, 60, 81)
        self.assertEqual(result.valid_rows, (0, 3, 0))
        self.assertEqual(result.mean_widths_px, (None, 8, None))
        self.assertEqual(result.left_boundary_x[0][0], None)
        self.assertEqual(result.right_boundary_x[0][2], None)

    def test_bright_filaments_on_dark_background_are_detected(self):
        pixels = bytes(220 - value for value in scene([(10, 8, 12)] * 2))
        result = measure_filaments(pixels, 90, 2, 0, 30, 60, 90)
        self.assertEqual(result.mean_widths_px, (10, 8, 12))
        self.assertEqual(result.valid_rows, (2, 2, 2))

    def test_transparent_profile_keeps_both_edge_rims_in_one_mask(self):
        row = bytearray([100] * 90)
        for base in (0, 30, 60):
            row[base + 8:base + 11] = bytes([20]) * 3
            row[base + 19:base + 22] = bytes([20]) * 3
        result = measure_filaments(bytes(row) * 2, 90, 2, 0, 30, 60, 90)
        self.assertEqual(result.widths_px, ((14, 14, 14),) * 2)
        self.assertEqual(result.centerline_x, ((15, 45, 75),) * 2)
        self.assertEqual(result.filament_mask[8:22], bytes([255]) * 14)
        self.assertEqual(sum(value == 255 for value in result.filament_mask), 84)

    def test_centerlines_follow_filaments_that_move_between_rows(self):
        pixels = bytearray()
        for left in (3, 12, 20):
            row = bytearray([220] * 90)
            for base in (0, 30, 60):
                row[base + left:base + left + 8] = bytes([30]) * 8
            pixels.extend(row)
        result = measure_filaments(bytes(pixels), 90, 3, 0, 30, 60, 90, gradient_window_size=1)
        self.assertEqual(result.centerline_x, ((7, 37, 67), (16, 46, 76), (24, 54, 84)))
        self.assertEqual(result.widths_px, ((8, 8, 8),) * 3)
        self.assertAlmostEqual(result.center_x[0], 47 / 3)

    def test_one_edge_has_no_fabricated_width_center_or_red_pixels(self):
        pixels = (bytes([220]) * 15 + bytes([0]) * 15) * 3
        result = measure_filaments(pixels, 90, 1, 0, 30, 60, 90)
        self.assertEqual(result.centerline_x, ((None, None, None),))
        self.assertEqual(result.center_x, (None, None, None))
        self.assertEqual(result.widths_px, ((None, None, None),))
        self.assertEqual(result.filament_mask, bytes(90))
        self.assertNotIn(b"\xff\x00\x00", result.processed_rgb)

    def test_one_broad_transition_and_one_pixel_spike_are_not_filaments(self):
        profiles = [bytes([220] * 10 + [200, 180, 160, 140] + [140] * 16),
                    bytes([220] * 10 + [0] + [220] * 19)]
        for profile in profiles:
            with self.subTest(profile=profile):
                result = measure_filaments(profile * 3, 90, 1, 0, 30, 60, 90, 10)
                self.assertEqual(result.widths_px, ((None, None, None),))
                self.assertEqual(result.valid_rows, (0, 0, 0))

    def test_background_brightening_after_filament_does_not_move_right_boundary(self):
        profile = bytes([100] * 5 + [70, 30] + [10] * 6 + [40, 80, 100]
                        + [100] * 5 + [130, 170, 220] + [220] * 6)
        result = measure_filaments(profile * 3, 90, 1, 0, 30, 60, 90, 20)
        self.assertEqual(result.left_boundary_x[0], (6, 36, 66))
        self.assertEqual(result.right_boundary_x[0], (14, 44, 74))
        self.assertEqual(result.centerline_x[0], (10, 40, 70))

    def test_peaks_are_selected_instead_of_last_above_threshold_sample(self):
        profile = bytes([220] * 5 + [190, 100, 40, 10] + [10] * 8
                        + [40, 130, 190, 220] + [220] * 9)
        result = measure_filaments(profile * 3, 90, 1, 0, 30, 60, 90, 20)
        self.assertEqual(result.left_boundary_x[0], (6, 36, 66))
        self.assertEqual(result.right_boundary_x[0], (18, 48, 78))
        self.assertEqual(result.widths_px[0], (12, 12, 12))

    def test_missed_soft_exit_does_not_pair_with_a_distant_background_ramp(self):
        profile = bytes([100] * 4 + [50, 10] + [10] * 4
                        + [20, 30, 40, 50, 60] + [60] * 9
                        + [100, 150, 200] + [200] * 3)
        result = measure_filaments(profile * 3, 90, 1, 0, 30, 60, 90, 20)
        self.assertEqual(result.valid_rows, (0, 0, 0))
        self.assertEqual(result.centerline_x[0], (None, None, None))

    def test_same_direction_groups_cannot_form_a_filament(self):
        profile = bytes([220] * 5 + [100] * 10 + [10] * 15)
        result = measure_filaments(profile * 3, 90, 1, 0, 30, 60, 90, 20)
        self.assertEqual(result.widths_px[0], (None, None, None))

    def test_bright_filament_ignores_later_darkening_background(self):
        dark = bytes([100] * 5 + [70, 30] + [10] * 6 + [40, 80, 100]
                     + [100] * 5 + [130, 170, 220] + [220] * 6)
        profile = bytes(255 - value for value in dark)
        result = measure_filaments(profile * 3, 90, 1, 0, 30, 60, 90, 20)
        self.assertEqual(result.widths_px[0], (8, 8, 8))

    def test_vertical_window_uses_current_pixel_and_nine_vertical_neighbors(self):
        pixels = bytes(value for y in range(12) for value in (10 * y, 10 * y + 1, 10 * y + 2))
        averaged, counts = vertical_average(pixels, 3, 12, 10)
        self.assertEqual(counts, (6, 7, 8, 9, 10, 10, 10, 9, 8, 7, 6, 5))
        self.assertEqual(list(averaged[:3]), [25, 26, 27])
        self.assertEqual(list(averaged[4 * 3:5 * 3]), [45, 46, 47])
        self.assertEqual(list(averaged[5 * 3:6 * 3]), [55, 56, 57])
        self.assertEqual(list(averaged[-3:]), [90, 91, 92])

    def test_window_one_disables_vertical_averaging(self):
        pixels = bytes([0, 30, 60, 90, 120, 150])
        averaged, counts = vertical_average(pixels, 2, 3, 1)
        self.assertEqual(list(averaged), list(pixels))
        self.assertEqual(counts, (1, 1, 1))

    def test_top_bottom_gradients_are_normalized_without_zero_padding(self):
        result = measure_filaments(scene([(10, 8, 12)] * 4), 90, 4, 0, 30, 60, 90, 219)
        self.assertEqual(result.valid_rows, (4, 4, 4))
        self.assertEqual(result.widths_px, ((10, 8, 12),) * 4)
        self.assertEqual(result.gradient_window_size, 10)
        self.assertEqual(result.gradient_window_rows, (4, 4, 4, 4))

    def test_single_row_noise_is_attenuated_below_gradient_threshold(self):
        pixels = bytearray([100] * (90 * 21))
        for start, end in ((10, 20), (41, 49), (69, 81)):
            pixels[10 * 90 + start:10 * 90 + end] = bytes(end - start)
        unfiltered = measure_filaments(bytes(pixels), 90, 21, 0, 30, 60, 90, 20, 1)
        filtered = measure_filaments(bytes(pixels), 90, 21, 0, 30, 60, 90, 20, 10)
        self.assertEqual(unfiltered.widths_px[10], (10, 8, 12))
        self.assertEqual(filtered.valid_rows, (0, 0, 0))
        self.assertEqual(filtered.widths_px, ((None, None, None),) * 21)

    def test_fractional_averages_are_not_rounded_before_gradient_comparison(self):
        profile = bytes(1 if value == 0 else 0 for value in scene([(10, 8, 12)]))
        pixels = bytes(90) + profile + bytes(90)
        below = measure_filaments(pixels, 90, 3, 0, 30, 60, 90, 0.3, 3)
        above = measure_filaments(pixels, 90, 3, 0, 30, 60, 90, 0.34, 3)
        self.assertEqual(below.widths_px[1], (10, 8, 12))
        self.assertEqual(above.widths_px[1], (None, None, None))

    def test_window_larger_than_image_uses_only_existing_rows(self):
        averaged, counts = vertical_average(bytes([0, 10, 30, 40, 60, 70]), 2, 3, 100)
        self.assertEqual(list(averaged), [30, 40, 30, 40, 30, 40])
        self.assertEqual(counts, (3, 3, 3))

    def test_gradient_window_requires_a_positive_integer(self):
        pixels = scene([(10, 8, 12)])
        for size in (0, -1, 2.5, True, "10"):
            with self.subTest(size=size), self.assertRaises(ValueError):
                measure_filaments(pixels, 90, 1, 0, 30, 60, 90, gradient_window_size=size)

    def test_invalid_image_geometry_and_thresholds_are_rejected(self):
        pixels = scene([(10, 8, 12)])
        for coordinates in ((-1, 30, 60, 90), (0, 30, 30, 90),
                            (0, 30, 60, 91), (True, 30, 60, 90)):
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                measure_filaments(pixels, 90, 1, *coordinates)
        for threshold in (-1, 256, float("nan"), float("inf")):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                measure_filaments(pixels, 90, 1, 0, 30, 60, 90, threshold)
        with self.assertRaises(ValueError):
            measure_filaments(pixels[:-1], 90, 1, 0, 30, 60, 90)


if __name__ == "__main__":
    unittest.main()
