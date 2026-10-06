"""Hardware-independent angular coverage for horizontal compass calibration."""
import math
from collections import Counter


class CompassCalibration:
    def __init__(self, target=500, declination=0):
        self.target = target
        self.declination = declination
        self.points = Counter()
        self.counts = [0] * 36
        self.samples = 0
        self.minimum = None
        self.maximum = None
        self.raw = None
        self.parameters = None
        self.angle = None

    def _angle(self, x, y):
        p = self.parameters
        x = (x - p['offset_x']) * p['scale_x']
        y = (y - p['offset_y']) * p['scale_y']
        if x == 0 and y == 0:
            return None
        return math.degrees(math.atan2(y, x) + self.declination) % 360

    def add(self, x, y):
        if not all(math.isfinite(v) and v != -4096 for v in (x, y)):
            return
        self.samples += 1
        self.raw = {'x': x, 'y': y}
        self.points[x, y] += 1
        minimum = {a: min(self.minimum[a], v) if self.minimum else v
                   for a, v in self.raw.items()}
        maximum = {a: max(self.maximum[a], v) if self.maximum else v
                   for a, v in self.raw.items()}
        changed = minimum != self.minimum or maximum != self.maximum
        self.minimum, self.maximum = minimum, maximum
        sx, sy = maximum['x'] - minimum['x'], maximum['y'] - minimum['y']
        if min(sx, sy) <= 0:
            return
        self.parameters = {'offset_x': (maximum['x'] + minimum['x']) / 2,
                           'offset_y': (maximum['y'] + minimum['y']) / 2,
                           'scale_x': (sx + sy) / (2 * sx),
                           'scale_y': (sx + sy) / (2 * sy)}
        self.angle = self._angle(x, y)
        if changed:
            self.counts = [0] * 36
            for (px, py), count in self.points.items():
                angle = self._angle(px, py)
                if angle is not None:
                    self.counts[int(angle // 10)] += count
        elif self.angle is not None:
            self.counts[int(self.angle // 10)] += 1

    @property
    def complete(self):
        return self.samples >= self.target and all(n >= 3 for n in self.counts)

    def snapshot(self):
        return {'samples': self.samples, 'target': self.target,
                'raw': self.raw, 'minimum': self.minimum, 'maximum': self.maximum,
                'angle_deg': self.angle, 'bin_width_deg': 10, 'required_per_bin': 3,
                'bin_counts': list(self.counts), 'parameters': self.parameters}
