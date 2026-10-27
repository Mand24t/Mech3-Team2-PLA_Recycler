"""Paper-inspired slice segmentation for three filament views.

Reference: Petsiuk and Pearce, arXiv:2012.00191, section 2.2.
Each image row is a cross-section of our vertical views. Its gradient is
computed after vertical sliding-window averaging (10 rows by default).
Signed gradient samples above the threshold form edge groups. A dominant
entrance and the following exit bound a filled filament mask; the mask's extent determines that row's centerline.

The paper does not prescribe this exact finite-difference/grouping/pairing rule.
These choices are documented in PAPER_METHOD.md. No optical calibration is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from array import array
from collections.abc import Sequence
import math


@dataclass(frozen=True)
class FilamentMeasurement:
    width: int
    height: int
    window_bounds: tuple[tuple[int, int], ...]
    center_x: tuple[float | None, ...]  # Mean of valid row centerlines, by view.
    centerline_x: tuple[tuple[float | None, ...], ...]  # [y][view]
    left_boundary_x: tuple[tuple[int | None, ...], ...]
    right_boundary_x: tuple[tuple[int | None, ...], ...]
    widths_px: tuple[tuple[int | None, ...], ...]  # [y][left, middle, right]
    mean_widths_px: tuple[float | None, ...]
    valid_rows: tuple[int, ...]
    gradient_threshold: float
    gradient_window_size: int
    gradient_window_rows: tuple[int, ...]  # Actual row count after clipping, indexed by y.
    gradient_mask: bytes  # 255 where an in-window adjacent-pixel gradient exceeds threshold.
    filament_mask: bytes  # 255 in each valid row interval [left, right); zero elsewhere.
    processed_rgb: bytes

    def ppm(self) -> bytes:
        return f"P6\n{self.width} {self.height}\n255\n".encode("ascii") + self.processed_rgb


def vertical_average(pixels: bytes, width: int, height: int,
                     window_size: int) -> tuple[Sequence[float], tuple[int, ...]]:
    """Average the current pixel and its vertical neighbors without padding.

    Even sizes have one extra row below: size 10 uses y-4 through y+5.
    Clipped windows are normalized by their actual counts, retaining fractions.
    Running column sums keep the work linear in the number of image pixels.
    """
    if type(window_size) is not int or window_size < 1:
        raise ValueError("Gradient window size must be a positive whole number.")
    if (type(width) is not int or type(height) is not int or width < 1 or height < 1
            or len(pixels) != width * height):
        raise ValueError("Grayscale payload does not match positive image dimensions.")
    if window_size == 1:
        return pixels, (1,) * height
    above, below = (window_size - 1) // 2, window_size // 2
    averaged = array("d", [0.0]) * (width * height)
    totals = [0] * width
    counts = []
    first = last = 0
    for y in range(height):
        wanted_first = max(0, y - above)
        wanted_last = min(height, y + below + 1)
        while first < wanted_first:
            offset = first * width
            for x in range(width):
                totals[x] -= pixels[offset + x]
            first += 1
        while last < wanted_last:
            offset = last * width
            for x in range(width):
                totals[x] += pixels[offset + x]
            last += 1
        count = last - first
        counts.append(count)
        offset = y * width
        for x in range(width):
            averaged[offset + x] = totals[x] / count
    return averaged, tuple(counts)


def slice_edges(pixels: Sequence[float], offset: int, start: int, end: int,
                threshold: float, gradient_mask: bytearray) -> tuple[int | None, int | None]:
    """Pair the dominant entrance transition with the next exit transition.

    Polarity follows the background at the region ends. Within a transition,
    mark the strongest gradient, rather than its last above-threshold pixel.
    A later background ramp must not replace the first exit from the filament.
    """
    groups = []
    current = None
    for x in range(start + 1, end):
        delta = pixels[offset + x] - pixels[offset + x - 1]
        magnitude = abs(delta)
        if magnitude > threshold:
            gradient_mask[offset + x] = 255
            sign = 1 if delta > 0 else -1
            if current is None or current[1] != x - 1 or current[2] != sign:
                current = [x, x, sign, x, magnitude]
                groups.append(current)
            else:
                current[1] = x
                if magnitude > current[4]:
                    current[3], current[4] = x, magnitude
        else:
            current = None
    if not groups:
        return None, None
    values = pixels[offset + start:offset + end]
    background = (values[0] + values[-1]) / 2
    # Retain bright-on-dark support; this setup normally chooses dark-on-light.
    entrance_sign = -1 if background - min(values) >= max(values) - background else 1
    entrances = [group for group in groups if group[2] == entrance_sign]
    if not entrances:
        return None, None
    entrance = max(entrances, key=lambda group: group[4])
    def intensity(value):
        return entrance_sign * value

    # Work in a polarity-normalized space: the filament is a positive ridge.
    # An internal texture edge must not count as an exit until most of the
    # entrance contrast has been recovered.
    baseline = intensity(pixels[offset + entrance[0] - 1])
    core_level = max(intensity(value) for value in values)
    recovery = (baseline + core_level) / 2
    exit_group = next((group for group in groups
                       if group[0] > entrance[1] and group[2] == -entrance_sign
                       and intensity(pixels[offset + group[1]]) <= recovery), None)
    if exit_group is None:
        return None, None
    # A transparent filament may contain two similarly dark rims separated by
    # background. Keep the second rim, but never extend to an isolated later
    # brightening ramp: another comparable entrance is required first.
    core = max(intensity(value) for value in values[entrance[0] - start:exit_group[0] - start])
    for following in groups:
        if following[0] <= exit_group[1] or following[2] != entrance_sign:
            continue
        if (following[4] < entrance[4] / 2
                or intensity(pixels[offset + following[1]]) < core - threshold):
            continue
        later_exit = next((group for group in groups
                           if group[0] > following[1] and group[2] == -entrance_sign
                           and intensity(pixels[offset + group[1]]) <= recovery), None)
        if later_exit is not None:
            exit_group = later_exit
    left, right = entrance[3], exit_group[3]
    # Adjacent peaks are a one-pixel spike, without room for a distinct center.
    if right - left < 2:
        return None, None
    # Check that this pair brackets the dark/bright body, rather than a weak
    # missed edge plus a distant background ramp. A factor-of-two asymmetry is
    # tolerated; greater imbalance is treated as an unreliable measurement.
    body = [left + x for x, value in enumerate(values[left - start:right - start])
            if intensity(value) > recovery]
    if not body:
        return None, None
    body_center = (body[0] + body[-1] + 1) / 2
    left_distance, right_distance = body_center - left, right - body_center
    if (min(left_distance, right_distance) <= 0
            or max(left_distance, right_distance) > 2 * min(left_distance, right_distance)):
        return None, None
    return left, right



def measure_filaments(pixels: bytes, width: int, height: int,
                      first_x: int, second_x: int, third_x: int, fourth_x: int,
                      gradient_threshold: float = 20,
                      gradient_window_size: int = 10) -> FilamentMeasurement:
    if type(width) is not int or type(height) is not int or width < 3 or height < 1:
        raise ValueError("Image dimensions must be positive integers; width must be at least 3.")
    if len(pixels) != width * height:
        raise ValueError("Grayscale payload does not match the image dimensions.")
    coordinates = (first_x, second_x, third_x, fourth_x)
    if (any(type(value) is not int for value in coordinates)
            or not 0 <= first_x < second_x < third_x < fourth_x <= width):
        raise ValueError("Region coordinates must satisfy 0 <= x1 < x2 < x3 < x4 <= image width.")
    threshold = float(gradient_threshold)
    if not math.isfinite(threshold) or not 0 <= threshold <= 255:
        raise ValueError("Gradient threshold must be between 0 and 255.")
    averaged, window_rows = vertical_average(pixels, width, height, gradient_window_size)
    bounds = tuple(zip(coordinates, coordinates[1:]))
    rgb = bytearray(width * height * 3)
    rgb[0::3] = pixels
    rgb[1::3] = pixels
    rgb[2::3] = pixels
    gradient_mask = bytearray(width * height)
    filament_mask = bytearray(width * height)
    left_rows, right_rows, center_rows, widths = [], [], [], []
    totals, center_totals, counts = [0, 0, 0], [0.0, 0.0, 0.0], [0, 0, 0]

    def mark(x: int, y: int):
        index = (y * width + x) * 3
        rgb[index:index + 3] = b"\xff\x00\x00"

    for y in range(height):
        offset = y * width
        row_left, row_right, row_centers, row_widths = [], [], [], []
        for region, (start, end) in enumerate(bounds):
            left, right = slice_edges(averaged, offset, start, end, threshold, gradient_mask)
            measured = center = None
            if left is not None and right is not None:
                measured = right - left
                center = (left + right) / 2.0
                filament_mask[offset + left:offset + right] = bytes([255]) * measured
                center_pixel = max(left + 1, min(right - 1, math.floor(center + 0.5)))
                mark(left, y)
                mark(center_pixel, y)
                mark(right, y)
                totals[region] += measured
                center_totals[region] += center
                counts[region] += 1
            row_left.append(left)
            row_right.append(right)
            row_centers.append(center)
            row_widths.append(measured)
        left_rows.append(tuple(row_left))
        right_rows.append(tuple(row_right))
        center_rows.append(tuple(row_centers))
        widths.append(tuple(row_widths))

    return FilamentMeasurement(
        width=width, height=height, window_bounds=bounds,
        center_x=tuple(total / count if count else None
                       for total, count in zip(center_totals, counts)),
        centerline_x=tuple(center_rows),
        left_boundary_x=tuple(left_rows), right_boundary_x=tuple(right_rows),
        widths_px=tuple(widths),
        mean_widths_px=tuple(total / count if count else None for total, count in zip(totals, counts)),
        valid_rows=tuple(counts), gradient_threshold=threshold,
        gradient_window_size=gradient_window_size, gradient_window_rows=window_rows,
        gradient_mask=bytes(gradient_mask), filament_mask=bytes(filament_mask),
        processed_rgb=bytes(rgb))
