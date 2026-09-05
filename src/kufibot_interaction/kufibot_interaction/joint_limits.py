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
    'neck': 60.0,
    'headLeftRight': 90.0,
    'eyeRight': 150.0,
    'eyeLeft': 30.0,
}


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
