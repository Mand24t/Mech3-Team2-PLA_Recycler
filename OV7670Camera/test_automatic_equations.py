"""Independent numerical checks for the specified proportional winding equations."""
import math
import time
import unittest

from automatic_equations import (AutomaticSettings, ProportionalPuller, linear_velocity,
                                 spooler_rpm, oscillator_speed, PULLER_WHEEL_DIAMETER_MM,
                                 DiameterWindow, deadband_error)


def sample(diameter, stamp, seq=1):
    return dict(protocol=1, valid=True, diameter_mm=diameter,
                frame_received_at=stamp, published_at=stamp, frame_sequence=seq)


class AutomaticEquationTests(unittest.TestCase):
    def test_direct_drive_velocity_and_spooler_units_and_gains(self):
        self.assertAlmostEqual(PULLER_WHEEL_DIAMETER_MM, 22.5044)
        # Sixty RPM is one wheel revolution per second, advancing one circumference.
        self.assertAlmostEqual(linear_velocity(60), math.pi * 22.5044)
        vf = linear_velocity(10)
        self.assertAlmostEqual(spooler_rpm(vf, 0), 10 * 22.5044 / 87)
        self.assertAlmostEqual(spooler_rpm(vf, 2, 1.5), 1.5 * 10 * 22.5044 / 94)
        self.assertLess(spooler_rpm(vf, 1), spooler_rpm(vf, 0))
        self.assertEqual(spooler_rpm(0, 0), 0)

    def test_oscillator_is_rad_per_second_converted_to_degrees(self):
        # Vf chosen to make the supplied expression exactly 1 rad/s at theta=0.
        vf = math.pi * 87 * 55 / 1.75
        self.assertAlmostEqual(oscillator_speed(vf, 0, 0), 180 / math.pi)
        self.assertAlmostEqual(oscillator_speed(vf, 0, 31.36),
                               (180 / math.pi) / math.cos(math.radians(31.36)))
        self.assertAlmostEqual(oscillator_speed(vf, 0, -31.36), oscillator_speed(vf, 0, 31.36))
        self.assertAlmostEqual(oscillator_speed(vf, 0, 0, 2), 360 / math.pi)
        self.assertLess(oscillator_speed(vf, 1, 0), oscillator_speed(vf, 0, 0))

    def test_proportional_base_error_gain_and_live_tuning_without_accumulation(self):
        now = time.monotonic()
        settings = AutomaticSettings(base=5, kp=5, slew=1000, deadband=0)
        puller = ProportionalPuller(settings)
        for seq in range(20):
            p = sample(1.95, now + seq * .01, seq)
            puller.update(p, now + seq * .01)
        now += .19
        self.assertAlmostEqual(puller.update(p, now), 6)
        for step in range(1, 10):
            self.assertAlmostEqual(puller.update(p, now + step / 10), 6)
        puller.settings = AutomaticSettings(base=5, kp=10, slew=1000, deadband=0)
        self.assertAlmostEqual(puller.update(p, now + 1), 7)
        self.assertAlmostEqual(puller.update(sample(1.55, now + 1.1, 20), now + 1.1), 6.8)
        for index in range(1, 20):
            puller.update(sample(1.55, now + 1.1 + index * .01, 20 + index), now + 1.1 + index * .01)
        self.assertAlmostEqual(puller.output, 3)
        # Lowering a live hard limit takes effect immediately, even with slow slew.
        puller.settings = AutomaticSettings(base=1, maximum=2, slew=.1)
        self.assertLessEqual(puller.update(sample(1.95, now + 1.5, 40), now + 1.5), 2)

    def test_window_is_last_twenty_unique_valid_frames_and_recovers_from_gaps(self):
        history = DiameterWindow()
        now = time.monotonic()
        p = sample(1.0, now, 1)
        for _ in range(25):
            history.observe(p, now)
        self.assertEqual(history.snapshot(now), (1.0, 1))
        history.observe(p | {"valid": False}, now)
        self.assertEqual(history.snapshot(now)[1], 1)
        for seq in range(2, 22):
            history.observe(sample(float(seq), now + seq * .01, seq), now + seq * .01)
        self.assertEqual(history.snapshot(now + .21), (11.5, 20))
        # Older packets from the other observer cannot be counted again.
        history.observe(p, now + .21)
        self.assertEqual(history.snapshot(now + .21), (11.5, 20))
        self.assertEqual(history.snapshot(now + 3), (None, 0))
        history.observe(sample(1.75, now + 3.1, 1), now + 3.1)
        self.assertEqual(history.snapshot(now + 3.1), (1.75, 1))

    def test_deadband_is_continuous_and_symmetric(self):
        for error in (-.02, -.01, 0, .01, .02):
            self.assertEqual(deadband_error(error, .02), 0)
        self.assertAlmostEqual(deadband_error(.05, .02), .03)
        self.assertAlmostEqual(deadband_error(-.05, .02), -.03)
        self.assertLess(deadband_error(.020001, .02), .000002)
        self.assertAlmostEqual(deadband_error(.05, 0), .05)

    def test_average_controls_error_after_warmup_and_stale_input_still_stops(self):
        now = time.monotonic()
        puller = ProportionalPuller(AutomaticSettings(base=5, kp=10, slew=1000, deadband=.02))
        for seq in range(19):
            self.assertAlmostEqual(puller.update(sample(1.80, now + seq * .01, seq), now + seq * .01), 5)
        self.assertAlmostEqual(puller.update(sample(1.80, now + .19, 19), now + .19), 5.3)
        self.assertAlmostEqual(puller.measurement, 1.80)
        self.assertEqual(puller.sample_count, 20)
        self.assertAlmostEqual(puller.effective_error, .03)
        # Twenty alternating noisy readings average exactly to the target.
        for seq in range(20, 40):
            diameter = 1.72 if seq % 2 == 0 else 1.78
            puller.update(sample(diameter, now + seq * .01, seq), now + seq * .01)
        self.assertAlmostEqual(puller.measurement, 1.75)
        self.assertEqual(puller.effective_error, 0)
        self.assertAlmostEqual(puller.output, 5)
        with self.assertRaises(ValueError):
            puller.update(sample(1.75, now + .39, 39), now + 3)

    def test_recalculated_frame_and_restarted_camera_reset_the_reference(self):
        history = DiameterWindow()
        now = time.monotonic()
        for seq in range(20):
            history.observe(sample(1.75, now + seq * .01, seq), now + seq * .01)
        history.observe(sample(1.8, now + .19, 19), now + .19)
        self.assertEqual(history.snapshot(now + .19), (1.8, 1))
        history.observe(sample(1.9, now + .20, 0), now + .20)
        self.assertEqual(history.snapshot(now + .20), (1.9, 1))

    def test_slew_bounds_zero_gains_and_stale_frame(self):
        now = time.monotonic()
        puller = ProportionalPuller(AutomaticSettings(base=5, kp=100, slew=2))
        self.assertAlmostEqual(puller.update(sample(10, now), now), 0.2)
        self.assertAlmostEqual(puller.update(sample(10, now), now + .1), 0.4)
        self.assertLessEqual(puller.output, 100)
        with self.assertRaises(ValueError):
            puller.update(sample(1.75, now), now + 3)
        AutomaticSettings(kp=0, spooler_gain=0, oscillator_gain=0).validate()
        for settings in (AutomaticSettings(maximum=101), AutomaticSettings(wheel_diameter=0),
                         AutomaticSettings(kp=-1), AutomaticSettings(oscillator_gain=1e10)):
            with self.assertRaises(ValueError):
                settings.validate()
        with self.assertRaises(ValueError):
            spooler_rpm(1, -1)


if __name__ == "__main__":
    unittest.main()
