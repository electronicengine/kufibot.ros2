"""Time-aligned sensor angles and startup-frame rigid transforms."""
from collections import deque
import math


def angle_delta(target, current):
    return (target-current+180.) % 360.-180.


def rotate_offset(x, y, heading_deg):
    """Robot-right/forward displacement to fixed map x/y; clockwise heading."""
    angle = math.radians(heading_deg)
    return [x*math.cos(angle)+y*math.sin(angle),
            -x*math.sin(angle)+y*math.cos(angle)]


def sensor_origin(pose, heading_deg, forward_m, lateral_m):
    offset = rotate_offset(lateral_m, forward_m, heading_deg)
    return [pose[0]+offset[0], pose[1]+offset[1]]


class SensorHistory:
    def __init__(self):
        self.samples = {}

    def add(self, name, stamp, value):
        if not math.isfinite(stamp):
            return
        if name == 'joints' and isinstance(value, dict):
            # Keep the complete optical posture with each time-aligned ray.
            # A partial JointState cannot establish a valid mapping pose.
            required = ('headLeftRight', 'neck', 'eyeLeft', 'eyeRight')
            value = ({key: value[key] for key in required}
                     if all(key in value for key in required) else None)
        history = self.samples.setdefault(name, deque(maxlen=128))
        if history and stamp < history[-1][0]:
            return  # Delayed packets must not rewind mapping orientation.
        if history and stamp == history[-1][0]:
            history.pop()
        history.append((stamp, dict(value) if isinstance(value, dict) else value))

    def at(self, name, stamp, max_skew):
        history = self.samples.get(name, ())
        before = after = None
        for sample in history:
            if sample[0] <= stamp:
                before = sample
            if sample[0] >= stamp:
                after = sample
                break
        if before and after:
            if stamp-before[0] > max_skew or after[0]-stamp > max_skew:
                return None
            if before[1] is None or after[1] is None:
                return None
            fraction = (stamp-before[0])/(after[0]-before[0]) if after[0] != before[0] else 0.
            if name == 'heading':
                return (before[1]+angle_delta(after[1], before[1])*fraction) % 360.
            if before[1].keys() != after[1].keys():
                return None
            return {key: value+(after[1][key]-value)*fraction for key, value in before[1].items()}
        nearest = before or after
        if nearest and abs(stamp-nearest[0]) <= max_skew:
            return nearest[1]
        return None
