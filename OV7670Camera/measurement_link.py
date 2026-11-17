"""Latest camera measurements over localhost TCP; never opens a hardware port."""
import json
import math
import socket
import socketserver
import threading
import time

ADDRESS = ("127.0.0.1", 47670)
MAX_AGE = 2.0


def live_diameter(packet, now=None):
    """Return a usable diameter in mm, or None for missing/invalid/stale data.

    Both processes run on this computer, so monotonic timestamps share a clock.
    Repeated transmissions do not make an old camera frame fresh.
    """
    now = time.monotonic() if now is None else now
    if not isinstance(packet, dict) or packet.get("protocol") != 1 or packet.get("valid") is not True:
        return None
    try:
        diameter = packet["diameter_mm"]
        stamps = (packet["frame_received_at"], packet["published_at"])
        if (type(diameter) not in (float, int) or not math.isfinite(diameter) or diameter <= 0
                or any(type(stamp) not in (float, int) or not math.isfinite(stamp)
                       or not 0 <= now - stamp <= MAX_AGE for stamp in stamps)):
            return None
        return diameter
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(0.5)
        publisher = self.server.publisher
        while not publisher.stop.is_set():
            with publisher.lock:
                payload = publisher.payload
            try:
                self.request.sendall(payload)
            except OSError:
                return
            publisher.stop.wait(0.1)


class MeasurementPublisher:
    def __init__(self, address=ADDRESS):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.payload = b'{"protocol":1,"valid":false,"reason":"Waiting for camera measurement"}\n'
        self.server = _Server(address, _Handler)
        self.server.publisher = self
        self.address = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def publish(self, packet):
        payload = (json.dumps(packet, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            self.payload = payload

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


class MeasurementReceiver:
    """Reconnect automatically and keep only the latest measurement."""
    def __init__(self, address=ADDRESS):
        self.address = address
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.packet = None
        self.status = "Waiting for camera viewer"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return (dict(self.packet) if self.packet is not None else None, self.status)

    def _run(self):
        while not self.stop.is_set():
            try:
                with socket.create_connection(self.address, timeout=0.5) as connection:
                    connection.settimeout(0.5)
                    buffer = b""
                    while not self.stop.is_set():
                        chunk = connection.recv(4096)
                        if not chunk:
                            raise OSError("Camera viewer disconnected")
                        buffer += chunk
                        if len(buffer) > 16384:
                            raise ValueError("Oversized camera message")
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            packet = json.loads(line)
                            if not isinstance(packet, dict) or packet.get("protocol") != 1:
                                raise ValueError("Unsupported camera message")
                            with self.lock:
                                self.packet = packet
                                self.status = "Camera viewer connected"
            except (OSError, ValueError, UnicodeError):
                with self.lock:
                    self.packet = None
                    self.status = "Waiting for camera viewer / reconnecting"
                self.stop.wait(0.5)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1.2)
