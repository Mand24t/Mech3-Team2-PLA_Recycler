"""CSV recording of displayed diameter readings; disk writes run off the GUI thread."""
import csv
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import time

RECORDING_DIRECTORY = Path(__file__).resolve().parent / "recordings"
DIAGNOSTIC_FIELDS = (
    ("statistics_window", "window_size"), ("session_sample_count", "sample_count"),
    ("window_sample_count", "window_count"), ("session_min_diameter_mm", "min_diameter"),
    ("session_max_diameter_mm", "max_diameter"), ("window_mean_diameter_mm", "mean_diameter"),
    ("mean_error_mm", "mean_error"), ("std_dev_mm", "std_dev"),
    ("mean_std_dev_mm", "mean_std_dev"), ("spread_95_half_width_mm", "spread_95"),
    ("mean_ci_95_half_width_mm", "ci_95"), ("mean_ci_95_lower_mm", "ci_lower"),
    ("mean_ci_95_upper_mm", "ci_upper"),
)
MOTOR_FIELDS = (
    "puller_command_rpm", "spooler_command_rpm", "extruder_command_percent",
    "cooling_fans_command_percent", "cooling_fans_command_pwm",
    "traverser_command_speed_deg_s", "traverser_command_angle_deg", "traverser_enabled",
    "filament_linear_velocity_mm_s", "puller_linear_velocity_mm_s",
    "extruded_length_mm",
    "mega_status_valid", "mega_status_age_s",
)
FIELDS = (("timestamp_utc", "elapsed_s", "frame_sequence", "mean_diameter_mm", "valid", "status")
          + tuple(name for name, key in DIAGNOSTIC_FIELDS) + MOTOR_FIELDS + ("new_diameter_sample",))
NEW_SAMPLE_COLUMN = FIELDS.index("new_diameter_sample")


class DiameterRecorder:
    def __init__(self, directory=RECORDING_DIRECTORY):
        self.directory = Path(directory)
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.path = None
        self.rows = 0
        self.points_saved = 0
        self.error = ""
        self.last_key = None
        self.last_diameter_key = None
        self.started_at = None
        self.stopped_at = None

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def recording(self):
        return self.busy and not self.stop_event.is_set()

    def start(self):
        if self.busy:
            raise RuntimeError("A recording is already active or finishing.")
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = self.directory / f"diameter_{stamp}_UTC.csv"
        stream = path.open("x", newline="", encoding="utf-8")
        try:
            writer = csv.writer(stream)
            writer.writerow(FIELDS)
            stream.flush()
        except Exception:
            stream.close()
            raise
        self.path, self.rows, self.error = path, 0, ""
        self.points_saved = 0
        self.last_key = None
        self.last_diameter_key = None
        self.started_at = time.monotonic()
        self.stopped_at = None
        self.pending = queue.Queue(maxsize=2048)
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._write, args=(stream, writer), daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            self.stop()
            stream.close()
            raise
        return path

    def record(self, packet, diameter_mm, status, diagnostics=None, motor_commands=None):
        if not self.recording:
            return
        valid = (type(diameter_mm) in (int, float)
                 and math.isfinite(diameter_mm) and diameter_mm > 0)
        packet = packet if isinstance(packet, dict) else {}
        # Heartbeat retransmissions and GUI polling must not multiply samples.
        # A validity/status transition is retained even without a new frame.
        motor_commands = motor_commands or {}
        diameter_key = packet.get("frame_sequence"), packet.get("frame_received_at")
        key = (diameter_key, valid, "" if valid else status,
               motor_commands.get("_status_received_at"), motor_commands.get("mega_status_valid"))
        if key == self.last_key:
            return
        row = (datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               f"{time.monotonic() - self.started_at:.6f}",
               packet.get("frame_sequence", ""),
               f"{diameter_mm:.4f}" if valid else "", int(valid), status)
        diagnostics = diagnostics if valid and diagnostics is not None else {}
        row += tuple("" if diagnostics.get(key) is None else f"{diagnostics[key]:.10g}"
                     for name, key in DIAGNOSTIC_FIELDS)
        new_diameter = valid and diameter_key != self.last_diameter_key
        row += tuple("" if motor_commands.get(name) is None else f"{motor_commands[name]:.10g}"
                     for name in MOTOR_FIELDS) + (int(new_diameter),)
        try:
            self.pending.put_nowait(row)
            self.last_key = key
            if new_diameter:
                self.last_diameter_key = diameter_key
        except queue.Full:
            with self.lock:
                self.error = "Recording stopped: disk writer could not keep up. Queued rows are being saved."
            self.stop()

    def _write(self, stream, writer):
        try:
            while not self.stop_event.is_set() or not self.pending.empty():
                try:
                    row = self.pending.get(timeout=0.1)
                except queue.Empty:
                    continue
                writer.writerow(row)
                stream.flush()
                with self.lock:
                    self.rows += 1
                    if row[NEW_SAMPLE_COLUMN]:
                        self.points_saved += 1
        except (OSError, ValueError, csv.Error) as error:
            with self.lock:
                self.error = f"Recording failed: {error}"
            self.stop()
        finally:
            try:
                stream.close()
            except OSError as error:
                with self.lock:
                    self.error = f"Could not finish recording: {error}"
            self.stop()

    def stop(self):
        with self.lock:
            if self.started_at is not None and self.stopped_at is None:
                self.stopped_at = time.monotonic()
        self.stop_event.set()

    def progress(self):
        with self.lock:
            end = self.stopped_at if self.stopped_at is not None else time.monotonic()
            elapsed = 0.0 if self.started_at is None else max(0.0, end-self.started_at)
            return elapsed, self.points_saved

    def snapshot(self):
        with self.lock:
            return self.rows, self.error


class RecordingExporter:
    """Copy a finished recording atomically, retaining the original as a backup."""
    def __init__(self):
        self.thread = None
        self.path = None
        self.error = ""

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, source, destination):
        if self.busy:
            raise RuntimeError("A recording is still being saved.")
        self.path, self.error = None, ""
        self.thread = threading.Thread(target=self._copy, args=(Path(source), Path(destination)), daemon=True)
        self.thread.start()

    def _copy(self, source, destination):
        temporary = None
        try:
            if source.resolve() == destination.resolve() or (destination.exists() and source.samefile(destination)):
                self.path = destination
                return
            # Replace only after the complete copy succeeds; an existing target
            # and the original recording remain intact if copying fails.
            with source.open("rb") as reader:
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".recording-", suffix=".tmp",
                                                 dir=destination.parent, delete=False) as writer:
                    temporary = Path(writer.name)
                    shutil.copyfileobj(reader, writer, length=1024*1024)
            os.replace(temporary, destination)
            temporary = None
            self.path = destination
        except (OSError, ValueError) as error:
            self.error = str(error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
