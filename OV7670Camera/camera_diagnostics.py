"""Run physical OV7670 diagnostics with the viewer disconnected."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import threading
import time
import tkinter as tk
import serial
from ov7670_viewer import CameraProtocol, CameraError


def diagnostic_reply(stream, command):
    stream.write(command.encode("ascii") + b"\n")
    deadline = time.monotonic() + 10
    line = bytearray()
    while time.monotonic() < deadline:
        byte = stream.read(1)
        if byte == b"\n":
            if line.startswith(b"OV7670_DIAG "):
                return json.loads(line[len(b"OV7670_DIAG "):])
            if line.startswith(b"OV7670_ERROR "):
                raise CameraError(line.decode("utf-8", errors="replace"))
            line.clear()
        elif byte:
            line.extend(byte)
            if len(line) > 8192:
                raise CameraError("Oversized diagnostic reply")
    raise CameraError("Diagnostic command timed out: " + command)


def frame_summary(frame):
    histogram = [0] * 256
    for value in frame.pixels:
        histogram[value] += 1
    return {
        "sequence": frame.sequence, "width": frame.width, "height": frame.height,
        "bytes": len(frame.pixels), "crc_valid": True,
        "min": min(frame.pixels), "max": max(frame.pixels),
        "mean": statistics.fmean(frame.pixels), "stddev": statistics.pstdev(frame.pixels),
        "unique_values": [value for value, count in enumerate(histogram) if count],
        "bit_high_counts": [sum(count for value, count in enumerate(histogram) if value & (1 << bit))
                            for bit in range(8)],
        "histogram": histogram,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM6")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent / "validation"
    directory.mkdir(exist_ok=True)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "port": args.port}
    images = {}
    stream = serial.Serial(port=None, baudrate=921600, timeout=0.1, write_timeout=2)
    stream.dtr = True
    stream.rts = True
    stream.port = args.port
    pattern_attempted = False
    try:
        stream.open()
        stream.reset_input_buffer()
        protocol = CameraProtocol(stream, threading.Event())
        report["identity"] = protocol.identify(timeout=10)
        print(json.dumps({"identity": report["identity"]}), flush=True)
        if not report["identity"]["camera_ready"]:
            raise CameraError("Camera not ready")
        report["normal_bus"] = diagnostic_reply(stream, "OV7670_DIAG")
        print(json.dumps({"normal_bus": report["normal_bus"]}), flush=True)
        normal = protocol.request_frame()
        report["normal_frame"] = frame_summary(normal)
        images["normal_before_test.png"] = normal
        pattern_attempted = True
        report["enable_pattern"] = diagnostic_reply(stream, "OV7670_TEST_ON")
        print(json.dumps({"enable_pattern": report["enable_pattern"]}), flush=True)
        if report["enable_pattern"]["result"] != 0:
            raise CameraError("Sensor did not accept its test-pattern command")
        time.sleep(0.2)
        for index in range(2):
            frame = protocol.request_frame()
            report[f"pattern_frame_{index}"] = frame_summary(frame)
            images[f"test_pattern_{index}.png"] = frame
        report["pattern_bus"] = diagnostic_reply(stream, "OV7670_DIAG")
        print(json.dumps({"pattern_bus": report["pattern_bus"]}), flush=True)
    except (OSError, CameraError, ValueError) as error:
        report["error"] = str(error)
        print(json.dumps({"error": str(error)}), flush=True)
    finally:
        if pattern_attempted and stream.is_open:
            try:
                report["disable_pattern"] = diagnostic_reply(stream, "OV7670_TEST_OFF")
                restored = CameraProtocol(stream, threading.Event()).request_frame()
                report["restored_frame"] = frame_summary(restored)
                images["normal_after_test.png"] = restored
            except (OSError, CameraError, ValueError) as error:
                report["restore_error"] = str(error)
        stream.close()
        (directory / "camera_diagnostics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if images:
        root = tk.Tk()
        root.withdraw()
        try:
            for filename, frame in images.items():
                image = tk.PhotoImage(master=root, data=frame.pgm(), format="PPM")
                image.write(str(directory / filename), format="png")
        finally:
            root.destroy()
    for key, value in report.items():
        if key.endswith("frame") or key.startswith("pattern_frame"):
            print(json.dumps({key: {field: data for field, data in value.items()
                                    if field != "histogram"}}), flush=True)
    print("Full report: " + str(directory / "camera_diagnostics.json"), flush=True)


if __name__ == "__main__":
    main()
