"""Validate optional compass coverage before forwarding it to clients."""
import math


def valid_coverage(data):
    if not isinstance(data, dict):
        return False
    finite = lambda v: type(v) in (int, float) and math.isfinite(v)
    for name in ('raw', 'minimum', 'maximum'):
        value = data.get(name)
        if value is not None and (not isinstance(value, dict) or
                                  not all(finite(value.get(a)) for a in ('x', 'y'))):
            return False
    if 'bin_counts' in data:
        counts = data['bin_counts']
        if (not isinstance(counts, list) or len(counts) != 36 or
                not all(type(n) is int and n >= 0 for n in counts) or
                data.get('bin_width_deg') != 10 or data.get('required_per_bin') != 3):
            return False
    angle = data.get('angle_deg')
    if angle is not None and (not finite(angle) or not 0 <= angle < 360):
        return False
    p = data.get('parameters')
    if p is not None and (not isinstance(p, dict) or not all(
            finite(p.get(key)) for key in ('offset_x', 'offset_y', 'scale_x', 'scale_y'))):
        return False
    if 'completed_at' in data and not isinstance(data['completed_at'], str):
        return False
    previous = data.get('last_result')
    if previous is not None:
        if not isinstance(previous, dict) or 'last_result' in previous:
            return False
        if not valid_coverage(previous):
            return False
    return True
