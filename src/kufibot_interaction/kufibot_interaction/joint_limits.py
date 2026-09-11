"""Servo joint limits shared by the agent gateway and arbiter."""

import math

JOINT_LIMITS = {
    'rightArm': (10.0, 72.0),
    'leftArm': (109.0, 180.0),
    'neck': (0.0, 120.0),
    'headLeftRight': (0.0, 180.0),
    'eyeRight': (140.0, 170.0),
    'eyeLeft': (0.0, 40.0),
}

NEUTRAL_ANGLES = {
    'rightArm': 15.0,
    'leftArm': 170.0,
    'neck': 10.0,
    'headLeftRight': 90.0,
    # Level optical pose. Navigation and mapping may only use sensor samples
    # captured at this posture.
    'eyeRight': 170.0,
    'eyeLeft': 0.0,
}

MAPPING_SENSOR_ANGLES = {
    'neck': 10.0,
    'eyeLeft': 0.0,
    'eyeRight': 170.0,
}

MAPPING_SENSOR_TOLERANCE_DEG = 1.0


def mapping_sensor_pose_valid(angles, tolerance_deg=MAPPING_SENSOR_TOLERANCE_DEG):
    """Whether a joint feedback sample is safe to use for mapping."""
    if not isinstance(angles, dict) or not math.isfinite(tolerance_deg) or tolerance_deg < 0:
        return False
    try:
        return all(math.isfinite(float(angles[name])) and
                   abs(float(angles[name]) - target) <= tolerance_deg
                   for name, target in MAPPING_SENSOR_ANGLES.items())
    except (KeyError, TypeError, ValueError):
        return False


def validate_joint_targets(names, angles):
    """Validate and clamp a parallel name/degree command."""
    if len(names) != len(angles) or not names:
        raise ValueError('names and angles_deg must be non-empty and equal length')
    applied, clamped = [], []
    for name, angle in zip(names, angles):
        try:
            numeric = float(angle)
        except (TypeError, ValueError) as error:
            raise ValueError(f'Invalid joint target: {name}') from error
        if name not in JOINT_LIMITS or not math.isfinite(numeric):
            raise ValueError(f'Invalid joint target: {name}')
        low, high = JOINT_LIMITS[name]
        safe = max(low, min(high, numeric))
        applied.append(safe)
        clamped.append(safe != numeric)
    return applied, clamped
