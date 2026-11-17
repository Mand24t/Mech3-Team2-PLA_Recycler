"""Automatic winding equations. Lengths are mm, linear speeds mm/s, angles degrees."""
from dataclasses import dataclass
from collections import deque
import math
import threading
import time

from OV7670Camera.measurement_link import live_diameter

MAX_RPM = 100.0  # CL42T puller limit.
MAX_DM_RPM = 20.0
PULLER_WHEEL_DIAMETER_MM = 0.886 * 25.4
SWEEP_LIMIT_DEG = 31.36
AVERAGE_WINDOW = 20


def deadband_error(error, half_width):
    """Continuous dead zone: zero inside the band, excess beyond its edge outside."""
    excess = abs(error) - half_width
    return math.copysign(excess, error) if excess > 1e-12 else 0.0


class DiameterWindow:
    """Thread-safe rolling history shared by the GUI and motor worker."""
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = deque(maxlen=AVERAGE_WINDOW)

    def _expire(self, now):
        if self.samples and now - self.samples[-1][0] > 2:
            self.samples.clear()

    def observe(self, packet, now=None):
        now = time.monotonic() if now is None else now
        diameter = live_diameter(packet, now)
        with self.lock:
            self._expire(now)
            if diameter is None:
                return
            stamp, seq = packet["frame_received_at"], packet.get("frame_sequence")
            if self.samples:
                previous_stamp, previous_seq, previous_diameter = self.samples[-1]
                if stamp < previous_stamp:
                    return  # A slower GUI/worker may still hold the previous frame.
                if (stamp, seq) == (previous_stamp, previous_seq):
                    if diameter == previous_diameter:
                        return
                    # Reprocessing/calibrating the same frame changes its scale.
                    # Do not mix that reference with the earlier measurements.
                    self.samples.clear()
                elif (type(seq) is int and type(previous_seq) is int and seq < previous_seq):
                    self.samples.clear()  # Camera restarted / frame counter wrapped.
            self.samples.append((stamp, seq, diameter))

    def clear(self):
        with self.lock:
            self.samples.clear()

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self._expire(now)
            count = len(self.samples)
            mean = math.fsum(item[2] for item in self.samples) / count if count else None
            return mean, count


def linear_velocity(puller_rpm, wheel_diameter_mm=PULLER_WHEEL_DIAMETER_MM):
    if not all(math.isfinite(x) for x in (puller_rpm, wheel_diameter_mm)) or puller_rpm < 0 or wheel_diameter_mm <= 0:
        raise ValueError("Use nonnegative puller RPM and a positive wheel diameter.")
    return math.pi * wheel_diameter_mm * puller_rpm / 60.0


def winding_diameter(n):
    if type(n) is not int or n < 0:
        raise ValueError("n must be a nonnegative whole number of completed round trips.")
    return 87.0 + 3.5 * n


def spooler_rpm(vf, n, gain=1.0):
    if not all(math.isfinite(x) for x in (vf, gain)) or vf < 0 or gain < 0:
        raise ValueError("Velocity and spooler gain must be nonnegative.")
    return gain * 60.0 * vf / (math.pi * winding_diameter(n))


def oscillator_speed(vf, n, theta_deg, gain=1.0):
    """Return angular speed magnitude in deg/s; supplied equation is rad/s."""
    if (not all(math.isfinite(x) for x in (vf, theta_deg, gain)) or vf < 0 or gain < 0
            or abs(theta_deg) > SWEEP_LIMIT_DEG + 0.001):
        raise ValueError("Invalid oscillator velocity, gain or angle.")
    radians_per_second = gain * 1.75 * vf / (
        math.pi * winding_diameter(n) * 55.0 * math.cos(math.radians(theta_deg)))
    return math.degrees(radians_per_second)


@dataclass(frozen=True)
class AutomaticSettings:
    target: float = 1.75
    base: float = 60.0
    kp: float = 1.0  # RPM per mm of diameter error.
    deadband: float = 0.1  # Half-width in mm; zero disables it.
    minimum: float = 0.0
    maximum: float = MAX_RPM
    slew: float = 2.0  # RPM/s; limits transitions while tuning.
    wheel_diameter: float = PULLER_WHEEL_DIAMETER_MM
    spooler_gain: float = 1.0
    oscillator_gain: float = 1.0

    def validate(self):
        if not all(math.isfinite(x) for x in vars(self).values()):
            raise ValueError("Automatic settings must be finite numbers.")
        if (self.target <= 0 or min(self.kp, self.spooler_gain, self.oscillator_gain) < 0
                or not 0 <= self.deadband < self.target
                or not 0 <= self.minimum < self.maximum <= MAX_RPM
                or not self.minimum <= self.base <= self.maximum
                or self.slew <= 0 or self.wheel_diameter <= 0):
            raise ValueError("Use a positive target/wheel diameter/slew, nonnegative gains, "
                             "0 <= deadband < target, and 0 <= min <= base <= max <= 100 RPM (min < max).")
        vf = linear_velocity(self.maximum, self.wheel_diameter)
        if (not math.isfinite(vf) or vf > 1000
                or oscillator_speed(vf, 0, SWEEP_LIMIT_DEG, self.oscillator_gain) > 360
                or not math.isfinite(spooler_rpm(vf, 0, self.spooler_gain))):
            raise ValueError("Settings exceed 1000 mm/s or 360 deg/s oscillator limits.")


class ProportionalPuller:
    def __init__(self, settings, initial_output=0.0, diameter_filter=None):
        settings.validate()
        self.settings = settings
        self.output = max(0.0, min(MAX_RPM, initial_output))
        self.last_key = None
        self.last_time = None
        self.measurement = None
        self.diameter_filter = diameter_filter if diameter_filter is not None else DiameterWindow()
        self.sample_count = 0
        self.error = self.effective_error = 0.0
        self.tick = None

    def update(self, packet, now=None):
        now = time.monotonic() if now is None else now
        measurement = live_diameter(packet, now)
        if measurement is None:
            self.diameter_filter.observe(packet, now)
            raise ValueError("Camera diameter missing, invalid, or stale.")
        key = (packet.get("frame_sequence"), packet["frame_received_at"])
        if key != self.last_key:
            stamp = packet["frame_received_at"]
            if self.last_time is not None and stamp <= self.last_time:
                raise ValueError("Camera timestamps moved backwards.")
            self.last_key, self.last_time = key, stamp
        self.diameter_filter.observe(packet, now)
        self.measurement, self.sample_count = self.diameter_filter.snapshot(now)
        if self.measurement is None:
            raise ValueError("No current valid diameter measurements for the control average.")
        # Proportional correction is not accumulated. Slew follows elapsed wall
        # time even if several control ticks use the same still-fresh frame.
        dt = 0.1 if self.tick is None else max(0.0, min(0.5, now - self.tick))
        self.tick = now
        s = self.settings
        self.error = self.measurement - s.target
        self.effective_error = deadband_error(self.error, s.deadband) if self.sample_count == AVERAGE_WINDOW else 0.0
        target = max(s.minimum, min(s.maximum, s.base + s.kp * self.effective_error))
        step = s.slew * dt
        self.output += max(-step, min(step, target - self.output))
        self.output = max(0.0, min(s.maximum, self.output))
        return self.output
