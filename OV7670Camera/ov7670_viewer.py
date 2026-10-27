"""USB grayscale viewer for a Nano ESP32 with a no-FIFO OV7670.

Run with the project's .venv-camera Python. No recycler modules are imported.
The Nano must implement the matching OV7670 USB protocol (see README.md).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import queue
import struct
import threading
import time
import zlib
import tkinter as tk
from tkinter import ttk

from filament_processing import measure_filaments
from diameter_geometry import diameter_scales, mean_diameter

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

MAGIC = b"OVF1"
HEADER = struct.Struct("<4sIHHII")
SIZES = {(160, 120), (320, 240), (640, 480)}
INFO_PREFIX = b"OV7670_INFO "
ERROR_PREFIX = b"OV7670_ERROR "
BAUD = 921600


class CameraError(RuntimeError):
    """Connection, device, or frame validation failed."""


@dataclass(frozen=True)
class Frame:
    sequence: int
    width: int
    height: int
    pixels: bytes
    received_at: float

    def pgm(self) -> bytes:
        return f"P5\n{self.width} {self.height}\n255\n".encode("ascii") + self.pixels


def validate_info(info: object) -> dict:
    if not isinstance(info, dict) or type(info.get("protocol")) is not int or info["protocol"] != 1:
        raise CameraError("Unsupported camera protocol. Use the matching NanoOV7670USB sketch.")
    if not isinstance(info.get("camera_ready"), bool):
        raise CameraError("Camera identity reply is missing its readiness status.")
    if info["camera_ready"]:
        if info.get("sensor_pid") != 0x76 or info.get("sensor") != "OV7670":
            raise CameraError("The board did not identify an OV7670 sensor.")
        if info.get("pixel_format") != "GRAY8":
            raise CameraError("The firmware is not providing 8-bit grayscale frames.")
        if (type(info.get("width")) is not int or type(info.get("height")) is not int
                or (info["width"], info["height"]) not in SIZES):
            raise CameraError("Unsupported image dimensions in the camera identity reply.")
    return info


def usb_ports() -> list:
    if list_ports is None:
        raise CameraError("pyserial is missing. Install the packages in requirements-camera.txt.")
    return sorted((p for p in list_ports.comports() if p.vid is not None),
                  key=lambda p: p.device)


def nano_candidate(port) -> bool:
    description = f"{port.description} {port.product or ''}".lower()
    return ((port.vid == 0x2341 and port.pid == 0x0070)
            or (port.vid == 0x303A and port.pid == 0x1001)
            or ("nano" in description and "esp32" in description))


class CameraProtocol:
    """Request/reply protocol on an already opened byte stream."""

    def __init__(self, stream, stop: threading.Event, baud: int = BAUD):
        self.stream = stream
        self.stop = stop
        self.baud = baud

    def read_exact(self, count: int, deadline: float) -> bytes:
        result = bytearray()
        while len(result) < count:
            if self.stop.is_set():
                raise CameraError("Connection cancelled.")
            if time.monotonic() >= deadline:
                raise CameraError("Timed out waiting for the camera. Check firmware, wiring, and baud rate.")
            chunk = self.stream.read(min(count - len(result), 65536))
            if chunk:
                result.extend(chunk)
        return bytes(result)

    def identify(self, timeout: float = 6.0) -> dict:
        deadline = time.monotonic() + timeout
        next_hello = 0.0
        line = bytearray()
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise CameraError("Connection cancelled.")
            if time.monotonic() >= next_hello:
                self.stream.write(b"OV7670_HELLO\n")
                next_hello = time.monotonic() + 1.0
            chunk = self.stream.read(1)
            if not chunk:
                continue
            if chunk == b"\n":
                if line.startswith(INFO_PREFIX):
                    try:
                        info = json.loads(bytes(line[len(INFO_PREFIX):]))
                    except (ValueError, UnicodeError) as error:
                        raise CameraError("Malformed camera identity reply.") from error
                    return validate_info(info)
                if line.startswith(ERROR_PREFIX):
                    raise CameraError(line[len(ERROR_PREFIX):].decode("utf-8", errors="replace"))
                line.clear()
            else:
                line.extend(chunk)
                if len(line) > 2048:
                    line.clear()
        raise CameraError("USB port opened, but no compatible camera firmware replied. "
                          "The OV7670 sensor itself is not a USB camera; the Nano needs the capture sketch.")

    def request_frame(self) -> Frame:
        self.stream.write(b"OV7670_FRAME\n")
        deadline = time.monotonic() + 12.0
        line = bytearray()
        window = bytearray()
        for _ in range(4096):
            byte = self.read_exact(1, deadline)
            window.extend(byte)
            window = window[-4:]
            if window == MAGIC:
                break
            if byte == b"\n":
                if line.startswith(ERROR_PREFIX):
                    raise CameraError(line[len(ERROR_PREFIX):].decode("utf-8", errors="replace"))
                line.clear()
            else:
                line.extend(byte)
        else:
            raise CameraError("Could not find a camera frame header.")
        header = MAGIC + self.read_exact(HEADER.size - len(MAGIC), deadline)
        _, sequence, width, height, length, checksum = HEADER.unpack(header)
        if (width, height) not in SIZES or length != width * height:
            raise CameraError("Invalid frame dimensions or payload length; frame discarded.")
        deadline = time.monotonic() + max(3.0, length * 10 / self.baud * 2 + 2)
        pixels = self.read_exact(length, deadline)
        if zlib.crc32(pixels) != checksum:
            raise CameraError("Frame checksum failed; frame discarded. Reconnect to retry.")
        return Frame(sequence, width, height, pixels, time.monotonic())


def put_latest(target: queue.Queue, value) -> None:
    try:
        target.put_nowait(value)
    except queue.Full:
        try:
            target.get_nowait()
        except queue.Empty:
            pass
        target.put_nowait(value)


class CameraWorker(threading.Thread):
    def __init__(self, port: str, baud: int, events: queue.Queue, frames: queue.Queue):
        super().__init__(name="OV7670 USB acquisition", daemon=True)
        self.port = port
        self.baud = baud
        self.events = events
        self.frames = frames
        self.stop = threading.Event()

    def run(self):
        connection = None
        try:
            if serial is None:
                raise CameraError("pyserial is not installed.")
            connection = serial.Serial(port=None, baudrate=self.baud, timeout=0.1,
                                       write_timeout=2, rtscts=False, dsrdtr=False)
            # Nano TinyUSB CDC requires both DTR and RTS; do not toggle them after opening.
            connection.dtr = True
            connection.rts = True
            connection.port = self.port
            connection.open()
            connection.reset_input_buffer()
            protocol = CameraProtocol(connection, self.stop, self.baud)
            info = protocol.identify()
            self.events.put(("identity", info))
            if not info["camera_ready"]:
                raise CameraError("Nano detected; camera not ready: " + str(info.get("error", "unknown error")))
            while not self.stop.is_set():
                put_latest(self.frames, protocol.request_frame())
        except (CameraError, OSError, ValueError) as error:
            if not self.stop.is_set():
                self.events.put(("error", str(error)))
        finally:
            if connection is not None:
                try:
                    if connection.is_open:
                        # A cancelled read may leave the Nano writing a frame.
                        # Drain it before dropping USB so the writer can finish.
                        deadline = time.monotonic() + 2.0
                        idle_deadline = time.monotonic() + 0.5
                        while time.monotonic() < min(deadline, idle_deadline):
                            chunk = connection.read(max(1, min(connection.in_waiting, 65536)))
                            if chunk:
                                idle_deadline = time.monotonic() + 0.5
                except (OSError, ValueError):
                    pass
                finally:
                    connection.close()
            self.events.put(("stopped", None))


class CollapsibleSection(ttk.Frame):
    """Hide the contents without destroying widgets or changing their values."""
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title = title
        self.expanded = True
        self.toggle_button = ttk.Button(self, text="- " + title,
                                        style="Section.TButton", command=self.toggle)
        self.toggle_button.pack(fill="x")
        self.content = ttk.Frame(self, padding=7)
        self.content.pack(fill="x")

    def toggle(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.content.pack(fill="x")
        else:
            self.content.pack_forget()
        self.toggle_button.configure(text=("- " if self.expanded else "+ ") + self.title)


class CameraViewer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.worker = None
        self.events = queue.Queue()
        self.frames = queue.Queue(maxsize=1)
        self.ports = {}
        self.last_frame = None
        self.photo = None
        self.processed_photo = None
        self.processed_bounds = None
        self.region_frame_width = 320
        self.gradient_threshold = 20.0
        self.gradient_window_size = 10
        self.window_size_error = ""
        self.threshold_error = ""
        self.processing_result = None
        self.processing_key = None
        self.processing_frame_sequence = None
        self.frame_started_at = None
        self.frame_count = 0
        self.closing = False
        self.accept_frames = False
        root.title("OV7670 | USB Camera Viewer")
        root.geometry(f"1200x{min(1080, root.winfo_screenheight() - 70)}")
        root.minsize(1000, 900)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.camera = tk.StringVar(value="OV7670")
        self.port = tk.StringVar()
        self.baud = tk.StringVar(value=str(BAUD))
        self.status = tk.StringVar(value="Disconnected. Connect the Nano by USB, then detect the camera.")
        self.identity = tk.StringVar(value="Sensor identity has not been read.")
        self.frame_status = tk.StringVar(value="No frame received.")
        self.region_x1 = tk.IntVar(value=20)
        self.region_x2 = tk.IntVar(value=106)
        self.region_x3 = tk.IntVar(value=213)
        self.region_x4 = tk.IntVar(value=320)
        self.region_coordinates = [self.region_x1, self.region_x2, self.region_x3, self.region_x4]
        self.gradient_threshold_input = tk.StringVar(value="20")
        self.gradient_window_size_input = tk.StringVar(value="10")
        self.processing_status = tk.StringVar(value="Waiting for a camera frame.")
        self.mean_width_vars = [tk.StringVar(value="N/A") for _ in range(3)]
        self.instantaneous_diameter = tk.StringVar(value="")
        self.geometry_inputs = {key: tk.StringVar(value=value) for key, value in (
            ("focal_length", "3.37"), ("sensor_width", "2.304"),
            ("lens_offset", "3.4"), ("filament_distance", ""),
            ("mirror_distance", ""), ("correction", "1.0"))}
        self.true_diameter = tk.StringVar(value="")
        self.direct_view = tk.StringVar(value="Middle")
        self.geometry_status = tk.StringVar(value="Enter installation distances to calculate diameter.")
        self.calibration_status = tk.StringVar(value="")
        self.base_diameter = None
        self.geometry_scales = None
        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="USB CAMERA VIEWER", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        style = ttk.Style(root)
        style.configure("Section.TButton", anchor="w", font=("Segoe UI", 10, "bold"))
        self.connection_section = CollapsibleSection(main, "Camera Connection")
        self.connection_section.pack(fill="x", pady=(8, 0))
        switch = ttk.Frame(self.connection_section.content)
        switch.pack(fill="x", pady=(8, 8))
        ttk.Label(switch, text="Camera:").pack(side="left")
        for title, value in [("Nano ESP32 + OV7670", "OV7670"), ("ESP32-CAM (next stage)", "ESP32-CAM")]:
            ttk.Radiobutton(switch, text=title, value=value, variable=self.camera,
                            command=self.camera_changed).pack(side="left", padx=10)
        panels = ttk.Frame(self.connection_section.content)
        panels.pack(fill="x")
        nano = ttk.LabelFrame(panels, text="Nano ESP32 + OV7670 / USB", padding=10)
        nano.pack(side="left", fill="both", expand=True, padx=(0, 8))
        ttk.Label(nano, text="USB serial port").grid(row=0, column=0, sticky="w")
        self.port_box = ttk.Combobox(nano, textvariable=self.port, state="readonly", width=35)
        self.port_box.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(nano, text="Baud rate").grid(row=1, column=0, sticky="w", pady=6)
        self.baud_box = ttk.Combobox(nano, textvariable=self.baud, state="readonly",
                                     values=("921600", "460800", "230400", "115200"), width=12)
        self.baud_box.grid(row=1, column=1, sticky="w", padx=8)
        nano.columnconfigure(1, weight=1)
        future = ttk.LabelFrame(panels, text="ESP32-CAM / USB", padding=10)
        future.pack(side="left", fill="both", expand=True)
        ttk.Label(future, text="Reserved for the second camera.\n"
                  "Only OV7670 capture is implemented in this version.",
                  wraplength=300).pack(anchor="w")
        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=10)
        self.detect_button = ttk.Button(buttons, text="Detect connected camera", command=self.detect)
        self.detect_button.pack(side="left")
        self.connect_button = ttk.Button(buttons, text="Connect selected port", command=self.connect)
        self.connect_button.pack(side="left", padx=8)
        self.disconnect_button = ttk.Button(buttons, text="Disconnect", command=self.disconnect)
        self.disconnect_button.pack(side="left")
        ttk.Label(main, textvariable=self.status, wraplength=990).pack(fill="x", anchor="w")
        ttk.Label(main, textvariable=self.identity, wraplength=990).pack(fill="x", anchor="w", pady=(3, 8))
        self.control_section = CollapsibleSection(main, "Control Panel")
        self.control_section.pack(fill="x", pady=(0, 10))
        boundaries = self.control_section.content
        slider_row = ttk.Frame(boundaries)
        slider_row.pack(fill="x")
        self.region_sliders = []
        for index, (label, variable, lower, upper) in enumerate([
            ("Region 1 x [px] (constant)", self.region_x1, 0, 317),
            ("Region 2 x [px] (constant)", self.region_x2, 1, 318),
            ("Region 3 x [px] (constant)", self.region_x3, 2, 319),
            ("Region 4 x [px] (constant)", self.region_x4, 3, 320),
        ]):
            cell = ttk.Frame(slider_row)
            cell.pack(side="left", fill="x", expand=True, padx=6)
            slider = tk.Scale(cell, label=label, variable=variable, from_=lower, to=upper,
                              resolution=1, orient="horizontal", showvalue=True,
                              command=lambda value, which=index: self.region_changed(which, value))
            slider.pack(fill="x")
            self.region_sliders.append(slider)
        threshold_row = ttk.Frame(boundaries)
        threshold_row.pack(fill="x", padx=6, pady=(7, 3))
        ttk.Label(threshold_row, text="Gradient Threshold [0-255] (constant)").pack(side="left")
        self.threshold_entry = ttk.Entry(threshold_row, textvariable=self.gradient_threshold_input, width=10)
        self.threshold_entry.pack(side="left", padx=10)
        ttk.Label(threshold_row, text="Gradient Window [px] (constant)").pack(side="left", padx=(10, 0))
        self.window_size_entry = ttk.Entry(threshold_row, textvariable=self.gradient_window_size_input, width=8)
        self.window_size_entry.pack(side="left", padx=10)
        ttk.Label(threshold_row, text="Search windows: x1-x2 | x2-x3 | x3-x4").pack(side="left", padx=15)
        geometry = ttk.Frame(boundaries)
        geometry.pack(fill="x", padx=6, pady=(7, 0))
        self.geometry_entries = {}
        fields = (
            ("Focal Length [mm] (constant)", "focal_length"),
            ("Active Sensor Width [mm] (constant)", "sensor_width"),
            ("Lens Reference Offset [mm] (constant)", "lens_offset"),
            ("Filament Distance [mm] (variable)", "filament_distance"),
            ("Mirror Apex Distance [mm] (constant)", "mirror_distance"),
            ("Correction Coefficient (variable)", "correction"))
        for index, (label, key) in enumerate(fields):
            row, column = divmod(index, 3)
            geometry.columnconfigure(column, weight=1, uniform="geometry")
            cell = ttk.Frame(geometry)
            cell.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=3)
            variable_control = key in ("filament_distance", "correction")
            label_options = {"foreground": "#c00000"} if variable_control else {}
            ttk.Label(cell, text=label, **label_options).pack(anchor="w")
            entry = ttk.Entry(cell, textvariable=self.geometry_inputs[key], width=14,
                              **label_options)
            entry.pack(fill="x")
            self.geometry_entries[key] = entry
        calibration = ttk.Frame(boundaries)
        calibration.pack(fill="x", padx=6, pady=(5, 3))
        ttk.Label(calibration, text="True Diameter [mm] (constant)").pack(side="left")
        self.true_diameter_entry = ttk.Entry(calibration, textvariable=self.true_diameter, width=12)
        self.true_diameter_entry.pack(side="left", padx=8)
        self.calibrate_button = ttk.Button(calibration, text="Set Correction",
                                          command=self.generate_correction, state="disabled")
        self.calibrate_button.pack(side="left")
        ttk.Label(calibration, text="Direct View (constant):").pack(side="left", padx=(15, 5))
        ttk.Combobox(calibration, textvariable=self.direct_view, state="readonly", width=8,
                     values=("Left", "Middle", "Right")).pack(side="left")
        ttk.Label(calibration, text="Mirror Angle: 45 degrees (constant)").pack(side="left", padx=12)
        ttk.Label(boundaries, text="Distances are from the sensor; mirror apex = mirror meeting point. Constants are editable setup values.", wraplength=1080).pack(anchor="w", padx=6)
        ttk.Label(boundaries, text="Focal length and lens offset are estimates; active sensor width assumes full-field capture.",
                  wraplength=1080).pack(anchor="w", padx=6)
        ttk.Label(boundaries, textvariable=self.geometry_status, wraplength=1080).pack(anchor="w", padx=6)
        ttk.Label(boundaries, textvariable=self.calibration_status, wraplength=1080).pack(anchor="w", padx=6)
        displays = ttk.Frame(main)
        displays.pack(fill="both", expand=True)
        displays.columnconfigure(0, weight=1, uniform="frame_views")
        displays.columnconfigure(1, weight=1, uniform="frame_views")
        displays.rowconfigure(0, weight=1)
        raw_panel = ttk.LabelFrame(displays, text="Raw frame", padding=5)
        raw_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        processed_panel = ttk.LabelFrame(displays, text="Processed view", padding=5)
        processed_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.canvas = tk.Canvas(raw_panel, width=320, height=120, background="#18212b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.processed_canvas = tk.Canvas(processed_panel, width=320, height=120, background="#18212b", highlightthickness=0)
        self.processed_canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.redraw)
        self.processed_canvas.bind("<Configure>", self.redraw)
        ttk.Label(main, textvariable=self.frame_status).pack(anchor="w", pady=(8, 0))
        ttk.Label(main, text="Blue: window limits and dividers. Red: per-row centerlines and paired gradient peaks."
                  ).pack(anchor="w")
        diagnostics = ttk.LabelFrame(main, text="Diagnostics Panel", padding=10)
        diagnostics.pack(fill="x", pady=(8, 0))
        ttk.Label(diagnostics, text="Average pixel width [px]").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.mean_width_entries = []
        for column, name in enumerate(("Left", "Middle", "Right")):
            diagnostics.columnconfigure(column, weight=1)
            ttk.Label(diagnostics, text=name).grid(row=1, column=column, sticky="w")
            entry = ttk.Entry(diagnostics, textvariable=self.mean_width_vars[column],
                              state="readonly", width=15)
            entry.grid(row=2, column=column, sticky="ew", padx=(0, 15), pady=3)
            self.mean_width_entries.append(entry)
        diagnostics.columnconfigure(3, weight=2)
        ttk.Label(diagnostics, text="Instantaneous Mean Diameter [mm]").grid(
            row=1, column=3, sticky="w")
        self.diameter_entry = ttk.Entry(diagnostics, textvariable=self.instantaneous_diameter,
                                       state="readonly", width=25)
        self.diameter_entry.grid(row=2, column=3, sticky="ew", pady=3)
        ttk.Label(diagnostics, textvariable=self.processing_status, wraplength=1100).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(5, 0))
        self.gradient_threshold_input.trace_add("write", self.threshold_changed)
        self.gradient_window_size_input.trace_add("write", self.window_size_changed)
        for variable in (*self.geometry_inputs.values(), self.true_diameter, self.direct_view):
            variable.trace_add("write", self.update_diameter)
        self.update_buttons()
        self.redraw()
        root.after(50, self.poll)

    def update_buttons(self):
        busy = self.worker is not None
        supported = self.camera.get() == "OV7670"
        for button in (self.detect_button, self.connect_button):
            button.config(state="normal" if supported and not busy else "disabled")
        self.disconnect_button.config(state="normal" if busy else "disabled")
        self.port_box.config(state="disabled" if busy or not supported else "readonly")
        self.baud_box.config(state="disabled" if busy or not supported else "readonly")

    def camera_changed(self):
        self.disconnect()
        self.clear_frame()
        self.identity.set("Sensor identity has not been read.")
        self.status.set("Disconnected." if self.camera.get() == "OV7670"
                        else "ESP32-CAM support will be added in the next stage.")
        self.update_buttons()

    def clear_frame(self):
        self.processing_result = None
        self.processing_key = None
        self.processing_frame_sequence = None
        self.last_frame = None
        self.photo = None
        self.frame_started_at = None
        self.frame_count = 0
        while not self.frames.empty():
            try:
                self.frames.get_nowait()
            except queue.Empty:
                break
        self.frame_status.set("No frame received.")
        self.redraw()

    def detect(self):
        try:
            ports = usb_ports()
        except (CameraError, OSError) as error:
            self.status.set(str(error))
            return
        self.ports = {f"{p.device} | {p.description}": p for p in ports}
        self.port_box["values"] = tuple(self.ports)
        if not ports:
            self.port.set("")
            self.status.set("No USB serial device found. Connect the Nano with a USB data cable and detect again.")
            return
        candidates = [p for p in ports if nano_candidate(p)]
        if len(candidates) == 1:
            match = next(label for label, p in self.ports.items() if p.device == candidates[0].device)
            self.port.set(match)
            self.connect()
        else:
            if self.port.get() not in self.ports:
                self.port.set(next(iter(self.ports)))
            self.status.set("Select the Nano's USB port, then Connect selected port. "
                            "Other boards are not probed automatically.")

    def connect(self):
        if self.worker is not None or self.camera.get() != "OV7670":
            return
        selected = self.ports.get(self.port.get())
        if selected is None:
            self.status.set("Detect USB ports first, then select the Nano ESP32.")
            return
        self.clear_frame()
        self.identity.set(f"USB board: {selected.device}; VID:PID "
                          f"{selected.vid:04X}:{selected.pid:04X}. Reading sensor identity...")
        self.status.set(f"Connecting to {selected.device}...")
        self.worker = CameraWorker(selected.device, int(self.baud.get()), self.events, self.frames)
        self.accept_frames = True
        self.worker.start()
        self.update_buttons()

    def disconnect(self):
        self.accept_frames = False
        if self.worker:
            self.worker.stop.set()
            self.status.set("Disconnecting...")
        self.clear_frame()

    def region_changed(self, which, value):
        requested = int(round(float(value)))
        lower = self.region_coordinates[which - 1].get() + 1 if which else 0
        upper = (self.region_coordinates[which + 1].get() - 1
                 if which < 3 else self.region_frame_width)
        coordinate = max(lower, min(requested, upper))
        if self.region_coordinates[which].get() != coordinate:
            self.region_coordinates[which].set(coordinate)
        self.redraw()

    def sync_region_width(self, width):
        if width == self.region_frame_width:
            return
        ratio = width / self.region_frame_width
        coordinates = []
        for index, variable in enumerate(self.region_coordinates):
            lower = coordinates[-1] + 1 if index else 0
            coordinates.append(max(lower, min(width - (3 - index), round(variable.get() * ratio))))
        self.region_frame_width = width
        for index, (slider, variable, coordinate) in enumerate(
                zip(self.region_sliders, self.region_coordinates, coordinates)):
            slider.configure(from_=index, to=width - (3 - index))
            variable.set(coordinate)

    def threshold_changed(self, *args):
        try:
            value = float(self.gradient_threshold_input.get())
            if not math.isfinite(value) or not 0 <= value <= 255:
                raise ValueError
        except ValueError:
            self.threshold_error = (
                f"Enter a threshold from 0 to 255; using {self.gradient_threshold:g}.")
            self.threshold_entry.state(["invalid"])
            self.update_processing_status()
            return
        self.threshold_error = ""
        self.threshold_entry.state(["!invalid"])
        changed = value != self.gradient_threshold
        self.gradient_threshold = value
        if changed:
            self.redraw()
        else:
            self.update_processing_status()

    def window_size_changed(self, *args):
        try:
            value = int(self.gradient_window_size_input.get())
            if value < 1:
                raise ValueError
        except ValueError:
            self.window_size_error = (
                f"Enter a positive whole-number window size; using {self.gradient_window_size}.")
            self.window_size_entry.state(["invalid"])
            self.update_processing_status()
            return
        self.window_size_error = ""
        self.window_size_entry.state(["!invalid"])
        changed = value != self.gradient_window_size
        self.gradient_window_size = value
        if changed:
            self.redraw()
        else:
            self.update_processing_status()

    def update_processing_status(self):
        if self.processing_result is None:
            status = "Waiting for a camera frame."
        else:
            counts = self.processing_result.valid_rows
            centers = [f"{value:.1f}" if value is not None else "N/A"
                       for value in self.processing_result.center_x]
            height = self.processing_result.height
            status = (f"Valid rows: Left {counts[0]}/{height}, Middle {counts[1]}/{height}, "
                      f"Right {counts[2]}/{height}. Mean center x: {centers[0]}, {centers[1]}, {centers[2]}. "
                      f"Applied threshold: {self.gradient_threshold:g}. Y window: {self.gradient_window_size}.")
        if self.threshold_error:
            status = self.threshold_error + " " + status
        if self.window_size_error:
            status = self.window_size_error + " " + status
        self.processing_status.set(status)

    def process_frame(self):
        key = (self.last_frame.sequence, self.last_frame.received_at,
               *(variable.get() for variable in self.region_coordinates),
               self.gradient_threshold, self.gradient_window_size)
        if key != self.processing_key:
            self.processing_result = measure_filaments(
                self.last_frame.pixels, self.last_frame.width, self.last_frame.height,
                *(variable.get() for variable in self.region_coordinates), self.gradient_threshold,
                gradient_window_size=self.gradient_window_size)
            self.processing_key = key
            self.processing_frame_sequence = self.last_frame.sequence
            for variable, mean in zip(self.mean_width_vars, self.processing_result.mean_widths_px):
                variable.set(f"{mean:.2f}" if mean is not None else "N/A")
        self.update_processing_status()
        self.update_diameter()

    def update_diameter(self, *args):
        self.base_diameter = None
        self.geometry_scales = None
        self.instantaneous_diameter.set("")
        self.calibrate_button.config(state="disabled")
        values = {}
        for key, variable in self.geometry_inputs.items():
            try:
                value = float(variable.get())
                if not math.isfinite(value) or value < 0 or (key != "lens_offset" and value == 0):
                    raise ValueError
                values[key] = value
                self.geometry_entries[key].state(["!invalid"])
            except ValueError:
                self.geometry_entries[key].state(["invalid"])
        try:
            true = float(self.true_diameter.get())
            if not math.isfinite(true) or true <= 0:
                raise ValueError
            self.true_diameter_entry.state(["!invalid"])
        except ValueError:
            true = None
            self.true_diameter_entry.state(["invalid"] if self.true_diameter.get() else ["!invalid"])
        if len(values) != len(self.geometry_inputs):
            self.geometry_status.set("Enter valid geometry and a positive correction coefficient; unknown distances are blank.")
            return
        try:
            if self.last_frame is None or self.processing_result is None:
                self.geometry_status.set("Geometry entered; waiting for a camera frame.")
                return
            self.geometry_scales = diameter_scales(
                self.last_frame.width, *(values[key] for key in (
                    "focal_length", "sensor_width", "lens_offset", "filament_distance", "mirror_distance")),
                direct_view=("Left", "Middle", "Right").index(self.direct_view.get()))
            self.base_diameter = mean_diameter(self.processing_result.mean_widths_px, self.geometry_scales)
            corrected = self.base_diameter * values["correction"]
            if not math.isfinite(corrected) or corrected <= 0:
                raise ValueError("Correction produces an invalid diameter.")
            self.instantaneous_diameter.set(f"{corrected:.4f}")
            scales = ", ".join(f"{scale:.6g}" for scale in self.geometry_scales)
            self.geometry_status.set(f"Calculated mm/pixel (Left, Middle, Right): {scales}.")
            fresh = time.monotonic() - self.last_frame.received_at <= 2
            if true is not None and fresh:
                self.calibrate_button.config(state="normal")
            if not fresh:
                self.geometry_status.set(self.geometry_status.get() + " Last frame is stale; calibration disabled.")
        except (ValueError, OverflowError) as error:
            self.base_diameter = None
            self.geometry_status.set(str(error))

    def generate_correction(self):
        # A normal push button: snapshot once, no latch or recurring callback.
        self.update_diameter()
        if "disabled" in self.calibrate_button.state() or self.base_diameter is None:
            return
        coefficient = float(self.true_diameter.get()) / self.base_diameter
        if not math.isfinite(coefficient) or coefficient <= 0:
            self.calibration_status.set("Cannot generate a finite positive correction coefficient.")
            return
        sequence = self.last_frame.sequence
        self.geometry_inputs["correction"].set(f"{coefficient:.15g}")
        self.calibration_status.set(f"Correction generated once from frame {sequence}: {coefficient:.8g}.")

    def draw_region_lines(self):
        self.processed_canvas.delete("region_boundary")
        if self.processed_bounds is None:
            return
        left, top, width, height = self.processed_bounds
        for index, variable in enumerate(self.region_coordinates):
            x = left + variable.get() * width / self.region_frame_width
            self.processed_canvas.create_line(
                x, top, x, top + height, fill="#0000ff", width=3,
                tags=("region_boundary", f"region_{index + 1}_boundary"))

    def redraw(self, event=None):
        self.processed_bounds = None
        for canvas in (self.canvas, self.processed_canvas):
            canvas.delete("all")
        if self.last_frame is None:
            self.processing_result = None
            self.processing_key = None
            self.processing_frame_sequence = None
            for variable in self.mean_width_vars:
                variable.set("N/A")
            self.update_processing_status()
            self.update_diameter()
            self.photo = self.processed_photo = None
            for canvas in (self.canvas, self.processed_canvas):
                canvas.create_text(max(1, canvas.winfo_width()) // 2,
                                   max(1, canvas.winfo_height()) // 2,
                                   text="Waiting for a camera frame", fill="#d5dce5",
                                   font=("Segoe UI", 15))
            return
        self.sync_region_width(self.last_frame.width)
        self.process_frame()
        original = tk.PhotoImage(master=self.root, data=self.last_frame.pgm(), format="PPM")
        processed = tk.PhotoImage(master=self.root, data=self.processing_result.ppm(), format="PPM")
        for canvas in (self.canvas, self.processed_canvas):
            image = original if canvas is self.canvas else processed
            width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
            factor = min(width / image.width(), height / image.height())
            if factor >= 2:
                image = image.zoom(int(factor))
            elif factor < 1:
                step = max(1, int(1 / factor) + 1)
                image = image.subsample(step)
            left, top = (width - image.width()) // 2, (height - image.height()) // 2
            canvas.create_image(left, top, anchor="nw", image=image)
            if canvas is self.canvas:
                self.photo = image
            else:
                self.processed_photo = image
                self.processed_bounds = (left, top, image.width(), image.height())
        self.draw_region_lines()

    def poll(self):
        if self.closing:
            return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "identity":
                version = value.get("sensor_ver")
                version_text = f"0x{version:02X}" if isinstance(version, int) else "unavailable"
                self.identity.set(f"{value.get('board', 'Nano ESP32')} | firmware {value.get('firmware', '?')} | "
                                  f"{value.get('sensor', 'unknown sensor')} | PID {value.get('sensor_pid')} | "
                                  f"sensor VER {version_text}")
                self.status.set("Connected. Receiving grayscale frames..." if value["camera_ready"]
                                else "USB board detected; camera initialization failed.")
            elif kind == "error":
                self.status.set(value)
            elif kind == "stopped":
                self.worker = None
                if self.status.get() == "Disconnecting...":
                    self.status.set("Disconnected.")
                self.update_buttons()
        try:
            frame = self.frames.get_nowait()
        except queue.Empty:
            frame = None
        if frame is not None and self.accept_frames:
            self.last_frame = frame
            self.frame_count += 1
            if self.frame_started_at is None:
                self.frame_started_at = frame.received_at
            self.redraw()
        if self.last_frame is not None:
            elapsed = time.monotonic() - self.frame_started_at
            fps = (self.frame_count - 1) / elapsed if elapsed > 0 else 0
            age = time.monotonic() - self.last_frame.received_at
            self.frame_status.set(f"Frame {self.last_frame.sequence} | {self.last_frame.width} x "
                                  f"{self.last_frame.height} | GRAY8 | received {fps:.1f} fps | "
                                  f"last received {age:.1f} s ago"
                                  + (" | STALE / disconnected" if age > 2 or self.worker is None else ""))
        self.update_diameter()
        self.root.after(50, self.poll)

    def close(self):
        self.closing = True
        if self.worker:
            self.worker.stop.set()
        self.root.after(50, self.finish_close)

    def finish_close(self):
        if self.worker and self.worker.is_alive():
            self.root.after(50, self.finish_close)
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-ports", action="store_true", help="List USB serial devices without opening them.")
    parser.add_argument("--connect", action="store_true", help="Detect and connect a single Nano at startup.")
    parser.add_argument("--first-x", type=int, help="Initial first region boundary in source pixels.")
    parser.add_argument("--second-x", type=int, help="Initial second region boundary in source pixels.")
    parser.add_argument("--third-x", type=int, help="Initial third region boundary in source pixels.")
    parser.add_argument("--fourth-x", type=int, help="Initial fourth region boundary in source pixels.")
    parser.add_argument("--threshold", type=float, help="Initial gradient threshold (0-255).")
    parser.add_argument("--gradient-window-size", type=int, default=10, help="Vertical averaging window in pixels (default 10).")
    for option in ("focal-length", "sensor-width", "lens-offset", "filament-distance",
                   "mirror-distance", "correction", "true-diameter"):
        parser.add_argument("--" + option, type=float, help="Initial " + option.replace("-", " ") + ".")
    args = parser.parse_args()
    if args.gradient_window_size < 1:
        parser.error("Gradient window size must be a positive whole number.")
    first = args.first_x if args.first_x is not None else 20
    second = args.second_x if args.second_x is not None else 106
    third = args.third_x if args.third_x is not None else 213
    fourth = args.fourth_x if args.fourth_x is not None else 320
    if not 0 <= first < second < third < fourth <= 320:
        parser.error("Initial boundaries must satisfy 0 <= x1 < x2 < x3 < x4 <= 320.")
    if args.threshold is not None and (not math.isfinite(args.threshold) or not 0 <= args.threshold <= 255):
        parser.error("Threshold must be a finite number from 0 to 255.")
    if args.list_ports:
        try:
            ports = usb_ports()
        except CameraError as error:
            parser.exit(1, str(error) + "\n")
        for port in ports:
            print(f"{port.device}: {port.description} [{port.vid:04X}:{port.pid:04X}]")
        if not ports:
            print("No USB serial devices found.")
        return
    root = tk.Tk()
    app = CameraViewer(root)
    app.region_x1.set(first)
    app.region_x2.set(second)
    app.region_x3.set(third)
    app.region_x4.set(fourth)
    if args.threshold is not None:
        app.gradient_threshold_input.set(f"{args.threshold:g}")
    app.gradient_window_size_input.set(str(args.gradient_window_size))
    for key, variable in (*app.geometry_inputs.items(), ("true_diameter", app.true_diameter)):
        value = getattr(args, key)
        if value is not None:
            variable.set(f"{value:.15g}")
    if args.connect:
        root.after(300, app.detect)
    root.mainloop()


if __name__ == "__main__":
    main()
