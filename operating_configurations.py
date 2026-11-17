"""Validated, atomic storage of named operating setups; never starts hardware."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import uuid

from automatic_equations import AutomaticSettings, MAX_RPM, MAX_DM_RPM
from spooling_control import SpoolGeometry
from system_diagnostics import MAX_WINDOW
from OV7670Camera.viewer_settings import validate_settings

CONFIGURATION_PATH = Path(__file__).resolve().with_name("operating_configurations.json")


def validate_configuration(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported operating configuration.")
    data = deepcopy(data)
    try:
        modes = data["modes"]
        if (modes["puller"] not in ("Manual", "Automatic", "Disabled")
                or modes["spooler"] not in ("Manual", "Automatic")
                or modes["traverser"] not in ("Manual", "Automatic")):
            raise ValueError("Invalid motor mode.")
        automatic_keys = {"target", "base", "kp", "deadband", "minimum", "maximum", "slew", "wheel_diameter"}
        if set(data["automatic_inputs"]) != automatic_keys:
            raise ValueError("Invalid automatic input fields.")
        if set(data["motors"]) != {"cl", "dm", "spark", "fan"}:
            raise ValueError("Invalid motor setpoint fields.")
        data["automatic_applied"] = {key: float(value) for key, value in data["automatic_applied"].items()}
        for fields in (data["automatic_inputs"], data["automatic_applied"]):
            if not {"target", "base", "kp", "deadband", "minimum", "maximum", "slew", "wheel_diameter"} <= set(fields):
                raise ValueError("Missing automatic settings.")
            AutomaticSettings(**{key: float(value) for key, value in fields.items()}).validate()
        for key, limit in (("cl", MAX_RPM), ("dm", MAX_DM_RPM), ("spark", 100), ("fan", 100)):
            motor = data["motors"][key]
            if not isinstance(motor["entry"], str):
                raise ValueError("Invalid motor entry.")
            values = float(motor["setpoint"]), float(motor["entry"])
            if not all(math.isfinite(value) and 0 <= value <= limit for value in values):
                raise ValueError(f"{key}: speed must be 0-{limit}.")
            if type(motor["reverse"]) is not bool:
                raise ValueError("Invalid reverse selection.")
            motor["setpoint"] = values[0]
        for key in ("servo_inputs", "servo_applied"):
            fields = data[key]
            if set(fields) == {"speed", "bounds", "start"}:
                minimum, maximum = -float(fields["bounds"]), float(fields["bounds"])
                if data["modes"]["traverser"] == "Automatic":
                    geometry = data["geometry_inputs" if key == "servo_inputs" else "geometry_applied"]
                    minimum, maximum = float(geometry["minimum_angle"]), float(geometry["maximum_angle"])
                fields = dict(speed=fields["speed"], minimum=minimum, maximum=maximum,
                              start=max(minimum, min(maximum, float(fields["start"]))))
                data[key] = fields
            if set(fields) != {"speed", "minimum", "maximum", "start"}:
                raise ValueError("Invalid sweep fields.")
            speed, minimum, maximum, start = (float(fields[k]) for k in ("speed", "minimum", "maximum", "start"))
            if (not all(math.isfinite(v) for v in (speed, minimum, maximum, start))
                    or not 0 < speed <= 360 or not -90 <= minimum < maximum <= 90 or not minimum <= start <= maximum):
                raise ValueError("Invalid sweep speed, limits or starting angle.")
        data["servo_applied"] = {key: float(value) for key, value in data["servo_applied"].items()}
        data.setdefault("servo_angle_input", str(data["servo_inputs"]["start"]))
        if not math.isfinite(float(data["servo_angle_input"])) or not -90 <= float(data["servo_angle_input"]) <= 90:
            raise ValueError("Invalid Set Angle value.")
        # Manual and automatic traversing share the visible Min/Max limits.
        data["geometry_inputs"].update(minimum_angle=data["servo_inputs"]["minimum"], maximum_angle=data["servo_inputs"]["maximum"])
        geometry_keys = {"core_diameter", "winding_width", "filament_diameter", "minimum_angle", "maximum_angle"}
        if not geometry_keys <= set(data["geometry_inputs"]):
            raise ValueError("Incomplete spool geometry.")
        SpoolGeometry(**{key: float(value) for key, value in data["geometry_inputs"].items()}).validate()
        geometry = SpoolGeometry(**{key: float(value) for key, value in data["geometry_applied"].items()})
        geometry.validate()
        data["geometry_applied"] = asdict(geometry)
        window = data["statistics_window"]
        draft = int(data["statistics_window_input"])
        if type(window) is not int or not 2 <= window <= MAX_WINDOW or not 2 <= draft <= MAX_WINDOW:
            raise ValueError("Invalid diagnostic window.")
        if not isinstance(data["mega_port"], str) or len(data["mega_port"]) > 256:
            raise ValueError("Invalid Mega port.")
        if data.get("camera") is not None:
            validate_settings(data["camera"])
        # Reject nonfinite numbers anywhere, including informational commands.
        json.dumps(data, allow_nan=False)
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError("Incomplete operating configuration.") from error
    return deepcopy(data)


class OperatingConfigurationStore:
    def __init__(self, path=CONFIGURATION_PATH):
        self.path = Path(path)

    def list(self):
        if not self.path.exists():
            return []
        if self.path.stat().st_size > 8*1024*1024:
            raise ValueError("Operating configuration file is too large.")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("profiles"), list):
            raise ValueError("Invalid operating configuration file.")
        names, identifiers = set(), set()
        try:
            for profile in data["profiles"]:
                self._validate_identity(profile["name"], profile["temperature_c"])
                if not isinstance(profile["id"], str) or not profile["id"]:
                    raise ValueError("Invalid configuration identifier.")
                if profile["name"].casefold() in names or profile["id"] in identifiers:
                    raise ValueError("Duplicate operating configurations.")
                names.add(profile["name"].casefold())
                identifiers.add(profile["id"])
                validate_configuration(profile["settings"])
        except (KeyError, TypeError) as error:
            raise ValueError("Invalid saved operating configuration.") from error
        return sorted(data["profiles"], key=lambda entry: entry["name"].casefold())

    @staticmethod
    def _validate_identity(name, temperature):
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ValueError("Use a configuration name of 1-100 characters.")
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature <= -273.15:
            raise ValueError("Enter a valid operating temperature in Celsius.")

    def _write(self, profiles):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(version=1, profiles=profiles), indent=2, allow_nan=False)+"\n", encoding="utf-8")
        temporary.replace(self.path)

    def save(self, name, temperature, settings, replace_id=None):
        name = name.strip()
        self._validate_identity(name, temperature)
        settings = validate_configuration(settings)
        profiles = self.list()
        if any(entry["id"] != replace_id and entry["name"].casefold() == name.casefold() for entry in profiles):
            raise ValueError("That name already exists. Choose another name.")
        if replace_id is not None and not any(entry["id"] == replace_id for entry in profiles):
            raise ValueError("Configuration no longer exists.")
        profile = dict(id=replace_id or uuid.uuid4().hex, name=name, temperature_c=temperature,
                       saved_at=datetime.now(timezone.utc).isoformat(), settings=settings)
        profiles = [entry for entry in profiles if entry["id"] != replace_id]
        profiles.append(profile)
        self._write(profiles)
        return profile

    def get(self, identifier):
        for profile in self.list():
            if profile["id"] == identifier:
                return profile
        raise ValueError("Select a saved operating configuration.")

    def duplicate(self, identifier, name):
        original = self.get(identifier)
        return self.save(name, original["temperature_c"], original["settings"])

    def rename(self, identifier, name):
        profiles = self.list()
        profile = next((entry for entry in profiles if entry["id"] == identifier), None)
        if profile is None:
            raise ValueError("Select a saved operating configuration.")
        name = name.strip()
        self._validate_identity(name, profile["temperature_c"])
        if any(entry["id"] != identifier and entry["name"].casefold() == name.casefold() for entry in profiles):
            raise ValueError("That name already exists. Choose another name.")
        profile["name"] = name
        self._write(profiles)
        return profile

    def delete(self, identifier):
        profiles = self.list()
        filtered = [entry for entry in profiles if entry["id"] != identifier]
        if len(filtered) == len(profiles):
            raise ValueError("Select a saved operating configuration.")
        self._write(filtered)
