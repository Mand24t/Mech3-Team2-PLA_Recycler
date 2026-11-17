"""Recording-session diameter statistics, independent of the motor control loop."""
from collections import deque
import math
from statistics import NormalDist, fmean, stdev

MAX_WINDOW = 1000

# Two-sided 95% Student t critical values for 1..30 degrees of freedom.
# Beyond 30, the fourth-order inverse-t expansion is accurate to < 1e-6 here.
_T95 = (
    12.706205, 4.302653, 3.182446, 2.776445, 2.570582, 2.446912,
    2.364624, 2.306004, 2.262157, 2.228139, 2.200985, 2.178813,
    2.160369, 2.144787, 2.131450, 2.119905, 2.109816, 2.100922,
    2.093024, 2.085963, 2.079614, 2.073873, 2.068658, 2.063899,
    2.059539, 2.055529, 2.051831, 2.048407, 2.045230, 2.042272,
)
Z95 = NormalDist().inv_cdf(.975)


def t95(degrees):
    if degrees < 1:
        raise ValueError("At least two readings are required.")
    if degrees <= len(_T95):
        return _T95[degrees - 1]
    z, d = Z95, degrees
    return (z + (z**3 + z) / (4*d) + (5*z**5 + 16*z**3 + 3*z) / (96*d*d)
            + (3*z**7 + 19*z**5 + 17*z**3 - 15*z) / (384*d**3)
            + (79*z**9 + 776*z**7 + 1482*z**5 - 1920*z**3 - 945*z) / (92160*d**4))


class RecordingStatistics:
    def __init__(self, window=20):
        self.window = 20
        self.set_window(window)
        self.active = False
        self._reset()

    def _reset(self):
        # N recent rolling standard deviations require at most 2*N-1 readings.
        self.samples = deque(maxlen=2*MAX_WINDOW-1)
        self.sample_count = 0
        self.minimum = self.maximum = None
        self.last_key = None
        self._cached = None

    def start(self):
        self._reset()
        self.active = True

    def stop(self):
        self.active = False

    def set_window(self, window):
        if type(window) is not int or not 2 <= window <= MAX_WINDOW:
            raise ValueError(f"Window must be 2-{MAX_WINDOW} points.")
        self.window = window
        self._cached = None

    def observe(self, packet, diameter, target):
        if not self.active or not isinstance(packet, dict):
            return False
        if (type(diameter) not in (int, float) or not math.isfinite(diameter) or diameter <= 0
                or type(target) not in (int, float) or not math.isfinite(target) or target <= 0):
            return False
        stamp = packet.get("frame_received_at")
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            return False
        key = packet.get("frame_sequence"), stamp
        if key == self.last_key or (self.last_key is not None and stamp < self.last_key[1]):
            return False
        self.last_key = key
        self.samples.append((diameter, diameter-target))
        self.sample_count += 1
        self.minimum = diameter if self.minimum is None else min(self.minimum, diameter)
        self.maximum = diameter if self.maximum is None else max(self.maximum, diameter)
        self._cached = None
        return True

    def snapshot(self):
        if self._cached is not None:
            return self._cached.copy()
        recent = list(self.samples)[-self.window:]
        n = len(recent)
        result = dict(window_size=self.window, sample_count=self.sample_count, window_count=n,
                      min_diameter=self.minimum, max_diameter=self.maximum,
                      mean_diameter=fmean(v[0] for v in recent) if n else None,
                      mean_error=fmean(v[1] for v in recent) if n else None,
                      std_dev=None, mean_std_dev=None, spread_95=None,
                      ci_95=None, ci_lower=None, ci_upper=None)
        if n >= 2:
            deviation = stdev(v[0] for v in recent)
            # Average the last N rolling sample deviations. Shift before summing
            # squares to retain precision when filament variation is very small.
            values = [v[0] for v in self.samples]
            origin = values[0]
            sums, squares = [0.0], [0.0]
            for value in values:
                shifted = value-origin
                sums.append(sums[-1] + shifted)
                squares.append(squares[-1] + shifted*shifted)
            deviations = []
            for end in range(max(2, len(values)-self.window+1), len(values)+1):
                start = max(0, end-self.window)
                count = end-start
                total = sums[end]-sums[start]
                variance = ((squares[end]-squares[start])-total*total/count)/(count-1)
                deviations.append(math.sqrt(max(0.0, variance)))
            half_width = t95(n-1) * deviation / math.sqrt(n)
            result.update(std_dev=deviation, mean_std_dev=fmean(deviations),
                          spread_95=Z95*deviation, ci_95=half_width,
                          ci_lower=result["mean_diameter"]-half_width,
                          ci_upper=result["mean_diameter"]+half_width)
        self._cached = result
        return result.copy()
