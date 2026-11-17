"""Five-second averaging with exact synthetic timestamps and pixel means."""
import unittest
from types import SimpleNamespace
from background_capture import BackgroundCapture


def frame(seq, at, pixels=(10, 30)):
    return SimpleNamespace(sequence=seq, received_at=at, width=2, height=1, pixels=bytes(pixels))


class BackgroundCaptureTests(unittest.TestCase):
    def test_every_unique_frame_in_window_contributes_and_fraction_is_retained(self):
        capture = BackgroundCapture(2, 1, 10)
        capture.add(frame(-1, 9.9, (255, 255)))
        for i in range(10):
            capture.add(frame(i, 10+i*.5, (i, i+20)))
        capture.add(frame(9, 14.5, (9, 29)))  # duplicate preview must not count twice
        capture.add(frame(99, 15.1, (255, 255)))
        result = capture.finish()
        self.assertEqual(result.frame_count, 10)
        self.assertEqual(result.pixels, (4.5, 24.5))

    def test_empty_partial_or_interrupted_stream_is_not_saved(self):
        for timestamps in ((), (0.1,), (0.1, .5), (0.1, 4.8)):
            capture = BackgroundCapture(2, 1, 0)
            for i, at in enumerate(timestamps):
                capture.add(frame(i, at))
            with self.assertRaises(ValueError):
                capture.finish()

    def test_resolution_change_aborts(self):
        capture = BackgroundCapture(2, 1, 0)
        value = frame(0, .1)
        value.width = 3
        with self.assertRaises(ValueError):
            capture.add(value)
