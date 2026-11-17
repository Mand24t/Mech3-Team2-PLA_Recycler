"""Local viewer setup, including the geometry and calibration coefficient."""
import json
import uuid
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().with_name("viewer_settings.json")
CONFIGURATION_REQUEST_PATH = SETTINGS_PATH.with_name("operating_configuration_request.json")
CONFIGURATION_RESULT_PATH = SETTINGS_PATH.with_name("operating_configuration_result.json")
GEOMETRY_KEYS = ("focal_length", "sensor_width", "lens_offset", "filament_distance", "mirror_distance", "correction")


def validate_settings(data):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Unsupported viewer settings version.")
    width, regions = data.get("frame_width"), data.get("regions")
    if (type(width) is not int or not 1 <= width <= 640
            or not isinstance(regions, list) or len(regions) != 6
            or any(type(x) is not int for x in regions)
            or any(not 0 <= regions[i] < regions[i + 1] <= width for i in (0, 2, 4))):
        raise ValueError("Invalid saved view regions.")
    geometry = data.get("geometry")
    if not isinstance(geometry, dict) or any(key not in geometry for key in GEOMETRY_KEYS):
        raise ValueError("Incomplete saved geometry.")
    texts = [geometry[key] for key in GEOMETRY_KEYS]
    texts += [data.get(key) for key in ("threshold", "gradient_window_size", "true_diameter")]
    if any(not isinstance(value, str) or len(value) > 80 for value in texts):
        raise ValueError("Invalid saved input fields.")
    if "polynomial_order" in data and (not isinstance(data["polynomial_order"], str) or len(data["polynomial_order"]) > 80):
        raise ValueError("Invalid saved polynomial order field.")
    if "transparent_filament_mode" in data and type(data["transparent_filament_mode"]) is not bool:
        raise ValueError("Invalid transparent filament mode.")
    if type(data.get("light_filament_mode")) is not bool or data.get("direct_view") not in ("Left", "Middle", "Right"):
        raise ValueError("Invalid saved view mode.")
    if ("illumination" in data and (type(data["illumination"]) is not int or not 0 <= data["illumination"] <= 255)
            or "baud" in data and data["baud"] not in ("921600", "460800", "230400", "115200")
            or "port" in data and (not isinstance(data["port"], str) or len(data["port"]) > 256)):
        raise ValueError("Invalid saved camera connection or LED setting.")
    return data


def load_settings(path=SETTINGS_PATH):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError("Viewer settings file is oversized.")
    return validate_settings(json.loads(path.read_text(encoding="utf-8")))


def save_settings(data, path=SETTINGS_PATH):
    validate_settings(data)
    write_json(data, path)


def write_json(data, path):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def request_configuration(settings, name, settings_path=SETTINGS_PATH, request_path=CONFIGURATION_REQUEST_PATH):
    """Persist for a closed viewer, and notify an open viewer without touching USB."""
    save_settings(settings, settings_path)
    identifier = uuid.uuid4().hex
    write_json(dict(version=1, id=identifier, name=name, settings=settings), request_path)
    return identifier
