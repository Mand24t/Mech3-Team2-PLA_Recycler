"""Verify CSV measurements, gaps, duplicate suppression and session isolation."""
import csv
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from diameter_recording import DiameterRecorder, RecordingExporter, FIELDS


class DiameterRecordingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.recorder = DiameterRecorder(self.folder.name)

    def tearDown(self):
        self.recorder.stop()
        if self.recorder.thread is not None:
            self.recorder.thread.join(timeout=2)
        self.folder.cleanup()

    def finish(self):
        self.recorder.stop()
        self.recorder.thread.join(timeout=2)
        self.assertFalse(self.recorder.busy)
        self.assertEqual(self.recorder.snapshot()[1], "")
        with self.recorder.path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            self.assertEqual(reader.fieldnames, list(FIELDS))
            return rows

    def test_csv_uses_display_precision_and_omits_repeated_polls(self):
        path = self.recorder.start()
        first = dict(frame_sequence=10, frame_received_at=123)
        for _ in range(5):
            self.recorder.record(first, 1.8765, "Live | camera frame 10")
        # Same sequence after a new camera session is a distinct frame.
        second = dict(frame_sequence=10, frame_received_at=456)
        self.recorder.record(second, 1.9000, "Live | camera frame 10")
        rows = self.finish()
        self.assertTrue(path.exists())
        self.assertEqual([row["mean_diameter_mm"] for row in rows], ["1.8765", "1.9000"])
        self.assertEqual([row["valid"] for row in rows], ["1", "1"])
        self.assertTrue(rows[0]["timestamp_utc"].endswith("+00:00"))
        self.assertGreaterEqual(float(rows[1]["elapsed_s"]), float(rows[0]["elapsed_s"]))

    def test_invalid_and_disconnected_intervals_are_blank_not_zero_or_held(self):
        self.recorder.start()
        frame = dict(frame_sequence=1, frame_received_at=123)
        self.recorder.record(frame, 1.75, "Live")
        for _ in range(3):
            self.recorder.record(frame, None, "Camera measurement stale or invalid")
        self.recorder.record(None, None, "Disconnected")
        self.recorder.record(dict(frame_sequence=2, frame_received_at=456), 1.76, "Live")
        rows = self.finish()
        self.assertEqual([row["valid"] for row in rows], ["1", "0", "0", "1"])
        self.assertEqual([row["mean_diameter_mm"] for row in rows], ["1.7500", "", "", "1.7600"])

    def test_stop_drains_file_and_a_new_session_does_not_overwrite_it(self):
        first = self.recorder.start()
        with self.assertRaises(RuntimeError):
            self.recorder.start()
        for number in range(25):
            self.recorder.record(dict(frame_sequence=number, frame_received_at=number), 1.75, "Live")
        self.assertEqual(len(self.finish()), 25)
        old_content = first.read_bytes()
        self.recorder.record(dict(frame_sequence=99), 2.0, "Stopped")
        second = self.recorder.start()
        self.assertNotEqual(first, second)
        self.recorder.record(dict(frame_sequence=0, frame_received_at=0), 1.8, "Live")
        self.assertEqual(len(self.finish()), 1)
        self.assertEqual(first.read_bytes(), old_content)

    def test_write_failure_is_reported_and_stops_recording(self):
        class BrokenWriter:
            def writerow(self, row):
                if row != FIELDS:
                    raise OSError("Disk full")
        with mock.patch("diameter_recording.csv.writer", return_value=BrokenWriter()):
            self.recorder.start()
            self.recorder.record(dict(frame_sequence=1), 1.75, "Live")
            deadline = time.monotonic() + 2
            while self.recorder.busy and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertFalse(self.recorder.recording)
        self.assertIn("Disk full", self.recorder.snapshot()[1])

    def test_progress_counts_saved_measurements_and_freezes_elapsed_on_stop(self):
        self.assertEqual(self.recorder.progress(), (0, 0))
        self.recorder.start()
        self.recorder.record(dict(frame_sequence=1, frame_received_at=1), 1.75, "Live")
        self.recorder.record(dict(frame_sequence=2, frame_received_at=2), None, "Disconnected")
        self.recorder.record(dict(frame_sequence=3, frame_received_at=3), 1.8, "Live")
        with mock.patch("diameter_recording.time.monotonic", return_value=self.recorder.started_at + 65.3):
            self.recorder.stop()
        rows = self.finish()
        elapsed, saved = self.recorder.progress()
        self.assertEqual(len(rows), 3)
        self.assertEqual(saved, 2)  # Invalid-status rows are not measurement points.
        self.assertAlmostEqual(elapsed, 65.3)
        with mock.patch("diameter_recording.time.monotonic", return_value=self.recorder.started_at + 200):
            self.assertAlmostEqual(self.recorder.progress()[0], 65.3)
        self.recorder.start()
        self.assertEqual(self.recorder.progress()[1], 0)
        self.assertIsNone(self.recorder.stopped_at)

    def test_motor_status_logs_without_camera_and_duplicate_polls_are_omitted(self):
        self.recorder.start()
        commands = dict(_status_received_at=1, mega_status_valid=1, puller_command_rpm=0,
                        spooler_command_rpm=4, filament_linear_velocity_mm_s=18.2)
        self.recorder.record(None, None, "Disconnected", motor_commands=commands)
        self.recorder.record(None, None, "Disconnected", motor_commands=commands)
        commands = commands | dict(_status_received_at=2, spooler_command_rpm=8,
                                   filament_linear_velocity_mm_s=36.4)
        self.recorder.record(None, None, "Disconnected", motor_commands=commands)
        rows = self.finish()
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["spooler_command_rpm"] for row in rows], ["4", "8"])
        self.assertEqual([row["filament_linear_velocity_mm_s"] for row in rows], ["18.2", "36.4"])
        self.assertTrue(all(row["mean_diameter_mm"] == "" for row in rows))
        self.assertTrue(all(row["new_diameter_sample"] == "0" for row in rows))
        self.assertEqual(self.recorder.progress()[1], 0)


class RecordingExporterTests(unittest.TestCase):
    def test_same_file_save_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"original.csv"
            source.write_text("header\n1.75\n")
            exporter = RecordingExporter()
            exporter.start(source, source)
            exporter.thread.join(timeout=2)
            self.assertEqual(exporter.path, source)
            self.assertEqual(exporter.error, "")
            self.assertEqual(source.read_text(), "header\n1.75\n")

    def test_failed_copy_preserves_original_destination_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory)/"original.csv", Path(directory)/"chosen.csv"
            source.write_text("complete original")
            destination.write_text("previous saved file")
            exporter = RecordingExporter()
            with mock.patch("diameter_recording.shutil.copyfileobj", side_effect=OSError("Disk full")):
                exporter.start(source, destination)
                exporter.thread.join(timeout=2)
            self.assertFalse(exporter.busy)
            self.assertIsNone(exporter.path)
            self.assertIn("Disk full", exporter.error)
            self.assertEqual(source.read_text(), "complete original")
            self.assertEqual(destination.read_text(), "previous saved file")
            self.assertEqual(list(Path(directory).glob(".recording-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
