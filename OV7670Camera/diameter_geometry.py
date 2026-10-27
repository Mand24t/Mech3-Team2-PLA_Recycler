"""Pinhole diameter conversion for two symmetric 45-degree plane mirrors.

The sensor is z=0, the lens reference plane is z=offset, the filament is
(x=0,z=L), and the mirror planes are z=A+x and z=A-x. Reflection puts the
virtual filament centers at (+/-(A-L), A), so both reflected views use depth A.
These are axial depths, not the lengths of the slanted light paths.
"""
import math


def diameter_scales(frame_width, focal_length, sensor_width, lens_offset,
                    filament_distance, mirror_distance, direct_view=1):
    """Return left/middle/right mm per output pixel under the pinhole model."""
    if type(frame_width) is not int or frame_width <= 0:
        raise ValueError("Frame width must be a positive integer.")
    if type(direct_view) is not int or direct_view not in (0, 1, 2):
        raise ValueError("Select the direct view: Left, Middle, or Right.")
    values = (focal_length, sensor_width, lens_offset, filament_distance, mirror_distance)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Geometry values must be finite numbers.")
    if focal_length <= 0 or sensor_width <= 0 or lens_offset < 0:
        raise ValueError("Focal length and sensor width must be positive; lens offset must be nonnegative.")
    if not lens_offset < filament_distance < mirror_distance:
        raise ValueError("Distances must satisfy: lens offset < filament < mirror meeting point.")
    pixel_pitch = sensor_width / frame_width
    scales = [pixel_pitch * (mirror_distance - lens_offset) / focal_length] * 3
    scales[direct_view] = pixel_pitch * (filament_distance - lens_offset) / focal_length
    if not all(math.isfinite(value) and value > 0 for value in scales):
        raise ValueError("Geometry produces an invalid scale.")
    return tuple(scales)


def mean_diameter(widths, scales):
    """Equal weight per view, independent of its number of valid image rows."""
    if len(widths) != 3 or len(scales) != 3:
        raise ValueError("Three valid filament views are required.")
    if any(value is None or not math.isfinite(value) or value <= 0 for value in (*widths, *scales)):
        raise ValueError("Three valid positive filament widths and scales are required.")
    result = sum(width * scale / 3 for width, scale in zip(widths, scales))
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Calculated diameter is invalid.")
    return result
