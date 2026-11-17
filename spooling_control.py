"""Linear level-winding geometry and local, motion-free geometry persistence."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().with_name("spooling_settings.json")
NOZZLE_TO_SPOOL_MM = 25 * 25.4  # Filament already between nozzle and spool at reset.


@dataclass(frozen=True)
class SpoolGeometry:
    core_diameter: float = 87.0
    winding_width: float = 55.0  # Clear distance between the inner flange faces.
    filament_diameter: float = 1.75
    minimum_angle: float = -31.0
    maximum_angle: float = 31.0
    motor_ratio: float = 1.0  # Motor revolutions per spool revolution.

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Spool geometry must contain finite measurements.")
        if (self.core_diameter <= 0 or not 0 < self.filament_diameter < self.winding_width
                or not -90 <= self.minimum_angle < self.maximum_angle <= 90 or self.motor_ratio != 1):
            raise ValueError("Use direct drive (ratio 1) and positive core/filament dimensions; winding width must "
                             "exceed filament diameter; angles must satisfy -90 <= min < max <= 90.")
        if self.angular_speed(20) > 360:
            raise ValueError("Geometry would require more than 360 deg/s at the spooler's 20 RPM limit.")

    @property
    def traverse(self):
        # Keep the filament center half a filament diameter inside each flange.
        return self.winding_width - self.filament_diameter

    def angular_speed(self, motor_rpm):
        return (abs(motor_rpm) / self.motor_ratio / 60.0 * self.filament_diameter
                * (self.maximum_angle - self.minimum_angle) / self.traverse)

    def radius(self, completed_layers):
        if type(completed_layers) is not int or completed_layers < 0:
            raise ValueError("Completed layers must be a nonnegative integer.")
        return self.core_diameter / 2.0 + completed_layers * self.filament_diameter

    def takeup_speed(self, motor_rpm, completed_layers):
        return 2.0 * math.pi * self.radius(completed_layers) * abs(motor_rpm) / self.motor_ratio / 60.0

    def target_motor_rpm(self, filament_speed, completed_layers, gain=1.0):
        return gain * 60.0 * filament_speed * self.motor_ratio / (2.0 * math.pi * self.radius(completed_layers))

    def command(self):
        self.validate()
        return "WINDCFG " + " ".join(format(v, ".8g") for v in asdict(self).values())


def save_geometry(geometry, path=SETTINGS_PATH):
    geometry.validate()
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(version=1, geometry=asdict(geometry)), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_geometry(path=SETTINGS_PATH):
    path = Path(path)
    if not path.exists():
        return SpoolGeometry()
    if path.stat().st_size > 8192:
        raise ValueError("Saved spool geometry is oversized.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("geometry"), dict):
        raise ValueError("Unsupported saved spool geometry.")
    try:
        geometry = SpoolGeometry(**(data["geometry"] | {"motor_ratio": 1.0}))
        geometry.validate()
    except (TypeError, OverflowError) as error:
        raise ValueError("Invalid saved spool geometry.") from error
    return geometry


class TakeupIntegrator:
    """Trapezoidal integration of commanded spool surface speed, in mm.

    Do not infer travel across missing telemetry / disconnected intervals.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.length_mm = 0.0
        self.previous = None

    @property
    def extruded_length_mm(self):
        return NOZZLE_TO_SPOOL_MM + self.length_mm

    def break_interval(self):
        self.previous = None

    def observe(self, timestamp, rpm, radius):
        if not all(math.isfinite(x) for x in (timestamp, rpm, radius)) or radius <= 0:
            raise ValueError("Invalid spool telemetry for length integration.")
        velocity = 2 * math.pi * radius * abs(rpm) / 60
        if self.previous is not None:
            previous_time, previous_velocity = self.previous
            elapsed = timestamp - previous_time
            if 0 < elapsed <= 2:
                self.length_mm += elapsed * (previous_velocity + velocity) / 2
        self.previous = timestamp, velocity
        return velocity, self.length_mm


def spool_rpm_command(geometry, layers, filament_speed, baseline_rpm=None, direction=1, limit=20.0):
    """One RPM calculation shared by the diagnostics preview and motor worker."""
    geometry.validate()
    if baseline_rpm is not None:
        if not math.isfinite(baseline_rpm) or not 0 <= baseline_rpm <= limit:
            raise ValueError("Spooler baseline must be between 0 and 20 RPM.")
        filament_speed = geometry.takeup_speed(baseline_rpm, 0)
    if not math.isfinite(filament_speed) or filament_speed < 0 or direction not in (-1, 1):
        raise ValueError("Invalid spooler velocity or direction.")
    target = geometry.target_motor_rpm(filament_speed, layers)
    return dict(rpm=direction * round(min(limit, target), 3), target=target, limited=target > limit)
