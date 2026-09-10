"""Shared STL-derived model coordinates (glTF Y-up metres)."""
import json
import math
from pathlib import Path

MODEL_DIRECTORY = Path(__file__).with_name('model')


def load_rig():
    return json.loads((MODEL_DIRECTORY / 'rig.json').read_text())


def joint_rotation(spec, angle):
    if not math.isfinite(angle):
        angle = spec['neutral_deg']
    low, high = spec['limits_deg']
    return math.radians((max(low, min(high, angle))-spec['assembly_deg']) * spec['multiplier'])
