"""Session lifecycle, sample identity, rolling statistics and confidence limits."""
import math
from statistics import mean, stdev
import unittest

from system_diagnostics import RecordingStatistics, t95, Z95


class RecordingStatisticsTests(unittest.TestCase):
    def sample(self, stats, number, diameter, target=2.0):
        return stats.observe(dict(frame_sequence=number, frame_received_at=float(number)), diameter, target)

    def test_starts_empty_counts_unique_valid_frames_and_freezes_on_stop(self):
        stats = RecordingStatistics(3)
        self.assertFalse(self.sample(stats, 1, 1.8))
        stats.start()
        self.assertTrue(self.sample(stats, 1, 1.8))
        self.assertIsNone(stats.snapshot()["std_dev"])
        self.assertFalse(self.sample(stats, 1, 1.8))
        self.assertFalse(self.sample(stats, 0, 1.9))
        self.assertFalse(self.sample(stats, 2, None))
        self.assertFalse(self.sample(stats, 2, float("nan")))
        self.assertEqual(stats.snapshot()["sample_count"], 1)
        self.assertTrue(self.sample(stats, 2, 1.9))
        before = stats.snapshot()
        stats.stop()
        self.assertFalse(self.sample(stats, 3, 2.5))
        self.assertEqual(stats.snapshot(), before)
        stats.start()
        self.assertEqual(stats.snapshot()["sample_count"], 0)
        self.assertIsNone(stats.snapshot()["min_diameter"])
        self.assertTrue(self.sample(stats, 2, 1.9))

    def test_rolling_error_deviation_smoothed_deviation_and_distinct_ci(self):
        stats = RecordingStatistics(3)
        stats.start()
        for number, value in enumerate((1., 2., 3., 4.), 1):
            self.sample(stats, number, value)
        result = stats.snapshot()
        self.assertEqual((result["min_diameter"], result["max_diameter"]), (1, 4))
        self.assertEqual(result["window_count"], 3)
        self.assertEqual(result["mean_diameter"], 3)
        self.assertEqual(result["mean_error"], 1)
        self.assertEqual(result["std_dev"], 1)
        self.assertAlmostEqual(result["mean_std_dev"], mean((stdev((1, 2)), 1, 1)))
        self.assertAlmostEqual(result["spread_95"], Z95)
        self.assertAlmostEqual(result["ci_95"], 4.302653/math.sqrt(3))
        self.assertAlmostEqual(result["ci_lower"], 3-result["ci_95"])
        self.assertAlmostEqual(result["ci_upper"], 3+result["ci_95"])
        stats.set_window(2)
        result = stats.snapshot()
        self.assertEqual(result["mean_error"], 1.5)
        self.assertAlmostEqual(result["std_dev"], math.sqrt(.5))
        self.assertAlmostEqual(result["mean_std_dev"], math.sqrt(.5))
        self.assertEqual(result["min_diameter"], 1)  # Session extrema are not window extrema.

    def test_zero_noise_and_target_at_sample_time(self):
        stats = RecordingStatistics()
        stats.start()
        self.sample(stats, 1, 1.75, 1.75)
        self.sample(stats, 2, 1.75, 1.8)
        result = stats.snapshot()
        self.assertAlmostEqual(result["mean_error"], -.025)
        for key in ("std_dev", "mean_std_dev", "spread_95", "ci_95"):
            self.assertEqual(result[key], 0)

    def test_long_recording_bounded_history_and_window_resize_keep_precision(self):
        stats = RecordingStatistics(1000)
        stats.start()
        for number in range(3000):
            self.sample(stats, number, 1.75+number*1e-9)
        self.assertLessEqual(len(stats.samples), 1999)
        result = stats.snapshot()
        expected = stdev(1.75+number*1e-9 for number in range(2000, 3000))
        self.assertAlmostEqual(result["std_dev"], expected, places=14)
        self.assertAlmostEqual(result["mean_std_dev"], expected, places=14)
        self.assertEqual(result["sample_count"], 3000)
        self.assertEqual(result["min_diameter"], 1.75)
        stats.set_window(20)
        self.assertEqual(stats.snapshot()["window_count"], 20)

    def test_t_critical_values_and_window_validation(self):
        self.assertAlmostEqual(t95(1), 12.706204736, places=5)
        self.assertAlmostEqual(t95(19), 2.093024054, places=5)
        self.assertAlmostEqual(t95(31), 2.039513446, places=6)
        self.assertAlmostEqual(t95(100), 1.983971519, places=6)
        for window in (1, 1001, 2.5, True):
            with self.assertRaises(ValueError):
                RecordingStatistics(window)


if __name__ == "__main__":
    unittest.main()
