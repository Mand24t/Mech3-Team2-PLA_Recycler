"""Protocol, acquisition, and hidden-window tests; no physical camera required."""
import json
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
import tkinter as tk
import ov7670_viewer as viewer


def identity(**changes):
    info = dict(protocol=1, board="Arduino Nano ESP32", firmware="test",
                camera_ready=True, sensor="OV7670", sensor_pid=0x76,
                sensor_ver=0x73, pixel_format="GRAY8", width=160, height=120)
    info.update(changes)
    return info


def packet(sequence=1, pixels=None, checksum=None, width=160, height=120):
    pixels = pixels if pixels is not None else bytes(range(256)) * 75
    checksum = viewer.zlib.crc32(pixels) if checksum is None else checksum
    return viewer.HEADER.pack(viewer.MAGIC, sequence, width, height, len(pixels), checksum) + pixels


class FakeUSB:
    def __init__(self, data=b"", info=None, automatic=False):
        self.incoming = bytearray(data)
        self.info = info or identity()
        self.automatic = automatic
        self.frame_count = 0
        self.closed = False
        self.is_open = False
        self.on_frame = None
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.incoming)

    def open(self):
        self.is_open = True

    def close(self):
        self.closed = True
        self.is_open = False

    def reset_input_buffer(self):
        self.incoming.clear()

    def write(self, data):
        self.writes.append(data)
        if self.automatic:
            if data == b"OV7670_HELLO\n":
                self.incoming.extend(viewer.INFO_PREFIX + json.dumps(self.info).encode() + b"\n")
            elif data == b"OV7670_FRAME\n":
                self.frame_count += 1
                self.incoming.extend(packet(sequence=self.frame_count))
                if self.on_frame:
                    self.on_frame(self.frame_count)
        return len(data)

    def read(self, count):
        count = min(count, 137, len(self.incoming))  # Deliberately fragment every payload.
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data


class ProtocolTests(unittest.TestCase):
    def protocol(self, data=b"", **kwargs):
        return viewer.CameraProtocol(FakeUSB(data, **kwargs), threading.Event())

    def test_identifies_sensor_after_boot_messages_and_fragmented_reply(self):
        payload = b"ESP32 boot log\r\n" + viewer.INFO_PREFIX + json.dumps(identity()).encode() + b"\n"
        self.assertEqual(self.protocol(payload).identify()["sensor_ver"], 0x73)

    def test_unknown_firmware_times_out_without_being_called_a_camera(self):
        with self.assertRaisesRegex(viewer.CameraError, "no compatible camera firmware"):
            self.protocol().identify(timeout=0.01)

    def test_incompatible_protocol_rejected(self):
        with self.assertRaises(viewer.CameraError):
            viewer.validate_info(identity(protocol=2))

    def test_wrong_sensor_rejected(self):
        with self.assertRaisesRegex(viewer.CameraError, "OV7670 sensor"):
            viewer.validate_info(identity(sensor="OV2640", sensor_pid=0x26))

    def test_rgb_capture_rejected(self):
        with self.assertRaisesRegex(viewer.CameraError, "grayscale"):
            viewer.validate_info(identity(pixel_format="RGB565"))

    def test_board_detected_but_camera_not_ready_is_distinct(self):
        info = identity(camera_ready=False, sensor="unidentified", sensor_pid=0, error="wiring")
        self.assertFalse(viewer.validate_info(info)["camera_ready"])

    def test_malformed_identity_rejected(self):
        with self.assertRaisesRegex(viewer.CameraError, "Malformed"):
            self.protocol(viewer.INFO_PREFIX + b"{broken json}\n").identify()

    def test_fragmented_frame_keeps_all_pixel_values(self):
        frame = self.protocol(packet()).request_frame()
        self.assertEqual((frame.width, frame.height, frame.sequence), (160, 120, 1))
        self.assertEqual(frame.pixels, bytes(range(256)) * 75)
        self.assertTrue(frame.pgm().startswith(b"P5\n160 120\n255\n"))

    def test_header_like_bytes_inside_image_are_not_treated_as_headers(self):
        pixels = (b"OVF1\n\x00\xff" * 3000)[:19200]
        pixels = pixels.ljust(19200, b"\x00")
        self.assertEqual(self.protocol(packet(pixels=pixels)).request_frame().pixels, pixels)

    def test_checksum_failure_rejected(self):
        with self.assertRaisesRegex(viewer.CameraError, "checksum"):
            self.protocol(packet(checksum=0)).request_frame()

    def test_oversized_frame_rejected_before_payload_read(self):
        header = viewer.HEADER.pack(viewer.MAGIC, 1, 65535, 65535, 0xFFFFFFFF, 0)
        with self.assertRaisesRegex(viewer.CameraError, "dimensions"):
            self.protocol(header).request_frame()

    def test_wrong_payload_length_rejected(self):
        with self.assertRaisesRegex(viewer.CameraError, "payload length"):
            self.protocol(packet(pixels=b"too short")).request_frame()

    def test_device_error_is_reported(self):
        with self.assertRaisesRegex(viewer.CameraError, "Capture timed out"):
            self.protocol(b"OV7670_ERROR Capture timed out\n").request_frame()

    def test_boot_noise_before_frame_is_skipped(self):
        self.assertEqual(self.protocol(b"boot noise\r\n" + packet()).request_frame().sequence, 1)

    def test_expired_reads_and_cancelled_reads_exit(self):
        protocol = self.protocol()
        with self.assertRaisesRegex(viewer.CameraError, "Timed out"):
            protocol.read_exact(1, time.monotonic() - 1)
        protocol.stop.set()
        with self.assertRaisesRegex(viewer.CameraError, "cancelled"):
            protocol.read_exact(1, time.monotonic() + 1)

    def test_only_latest_frame_is_retained(self):
        target = queue.Queue(maxsize=1)
        viewer.put_latest(target, "old")
        viewer.put_latest(target, "new")
        self.assertEqual(target.get_nowait(), "new")
        self.assertTrue(target.empty())


class DiscoveryAndWorkerTests(unittest.TestCase):
    def test_detection_excludes_bluetooth_and_keeps_usb(self):
        bluetooth = SimpleNamespace(device="COM5", vid=None)
        nano = SimpleNamespace(device="COM8", vid=0x2341)
        with mock.patch.object(viewer.list_ports, "comports", return_value=[bluetooth, nano]):
            self.assertEqual(viewer.usb_ports(), [nano])

    def test_nano_candidates_do_not_include_mega_or_generic_adapters(self):
        nano = SimpleNamespace(description="USB Serial Device", product=None, vid=0x2341, pid=0x0070)
        mega = SimpleNamespace(description="Arduino Mega", product=None, vid=0x2341, pid=0x0042)
        generic = SimpleNamespace(description="CP210x", product=None, vid=0x10C4, pid=0xEA60)
        self.assertTrue(viewer.nano_candidate(nano))
        self.assertFalse(viewer.nano_candidate(mega))
        self.assertFalse(viewer.nano_candidate(generic))

    def test_worker_handshake_frames_cancellation_and_port_release(self):
        events, frames = queue.Queue(), queue.Queue(maxsize=1)
        worker = viewer.CameraWorker("TEST", viewer.BAUD, events, frames)
        stream = FakeUSB(automatic=True)
        stream.on_frame = lambda number: worker.stop.set() if number == 3 else None
        with mock.patch.object(viewer.serial, "Serial", return_value=stream):
            worker.start()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(stream.closed)
        self.assertFalse(stream.is_open)
        self.assertEqual(len(stream.incoming), 0)  # Cancelled frame drained before close.
        self.assertTrue(stream.dtr)  # Native USB CDC requires DTR.
        self.assertTrue(stream.rts)
        self.assertEqual(frames.get_nowait().sequence, 2)
        self.assertEqual([events.get_nowait()[0], events.get_nowait()[0]], ["identity", "stopped"])

    def test_not_ready_sensor_never_requests_a_frame(self):
        events = queue.Queue()
        worker = viewer.CameraWorker("TEST", viewer.BAUD, events, queue.Queue(maxsize=1))
        stream = FakeUSB(info=identity(camera_ready=False, error="No sensor"), automatic=True)
        with mock.patch.object(viewer.serial, "Serial", return_value=stream):
            worker.start()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(stream.frame_count, 0)
        self.assertTrue(stream.closed)
        self.assertEqual([events.get_nowait()[0] for _ in range(3)], ["identity", "error", "stopped"])


class WindowTests(unittest.TestCase):
    def test_geometry_defaults_and_single_press_calibration(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = viewer.CameraViewer(root)
            self.assertEqual(app.geometry_inputs["focal_length"].get(), "3.37")
            self.assertEqual(app.geometry_inputs["sensor_width"].get(), "2.304")
            self.assertEqual(app.geometry_inputs["lens_offset"].get(), "3.4")
            self.assertIn("disabled", app.calibrate_button.state())
            app.last_frame = viewer.Frame(1, 320, 240, bytes(320 * 240), time.monotonic())
            app.processing_result = SimpleNamespace(mean_widths_px=(10, 20, 10))
            for key, value in (("focal_length", "4"), ("lens_offset", "5"),
                               ("filament_distance", "55"), ("mirror_distance", "105")):
                app.geometry_inputs[key].set(value)
            self.assertAlmostEqual(float(app.instantaneous_diameter.get()), 1.8)
            app.true_diameter.set("2")
            self.assertNotIn("disabled", app.calibrate_button.state())
            app.calibrate_button.invoke()
            coefficient = app.geometry_inputs["correction"].get()
            self.assertAlmostEqual(float(coefficient), 2 / 1.8)
            self.assertEqual(app.instantaneous_diameter.get(), "2.0000")
            app.processing_result = SimpleNamespace(mean_widths_px=(20, 40, 20))
            app.update_diameter()
            self.assertEqual(app.geometry_inputs["correction"].get(), coefficient)
            self.assertEqual(app.instantaneous_diameter.get(), "4.0000")
            app.calibrate_button.invoke()
            self.assertAlmostEqual(float(app.geometry_inputs["correction"].get()), 2 / 3.6)
            app.geometry_inputs["correction"].set("nan")
            self.assertEqual(app.instantaneous_diameter.get(), "")
            self.assertIn("disabled", app.calibrate_button.state())
            app.geometry_inputs["correction"].set("1")
            app.last_frame = viewer.Frame(2, 320, 240, bytes(320 * 240), time.monotonic() - 3)
            app.update_diameter()
            self.assertIn("disabled", app.calibrate_button.state())
            app.clear_frame()
            self.assertEqual(app.instantaneous_diameter.get(), "")
        finally:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()

    def test_grayscale_rendering_detection_and_camera_selector(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = viewer.CameraViewer(root)
            root.update_idletasks()
            pixels = bytes(0 if 20 <= y < 30 or 55 <= y < 65 or 90 <= y < 100 else 240
                           for y in range(120) for _ in range(160))
            app.last_frame = viewer.Frame(1, 160, 120, pixels, time.monotonic())
            app.redraw()
            root.update_idletasks()
            self.assertIsNotNone(app.photo)
            with mock.patch.object(viewer, "usb_ports", return_value=[]):
                app.detect()
            self.assertIn("No USB serial", app.status.get())
            app.camera.set("ESP32-CAM")
            app.camera_changed()
            self.assertEqual(str(app.detect_button["state"]), "disabled")
            self.assertIsNone(app.last_frame)
            app.camera.set("OV7670")
            app.camera_changed()
            self.assertEqual(str(app.detect_button["state"]), "normal")
        finally:
            for timer in root.tk.call("after", "info"):
                root.after_cancel(timer)
            root.destroy()


if __name__ == "__main__":
    unittest.main()
