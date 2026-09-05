"""Load and validate the shared DC motor speed limits."""

import json
import math
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).with_name('dc_motor_config.json')


def load_speed_limits(path=DEFAULT_CONFIG_PATH):
    """Return positive min/max linear and angular limits from JSON."""
    path = Path(path)
    try:
        with path.open(encoding='utf-8') as config_file:
            config = json.load(config_file)
        min_linear = float(config['min_linear_mps'])
        max_linear = float(config['max_linear_mps'])
        min_angular = float(config['min_angular_rps'])
        max_angular = float(config['max_angular_rps'])
    except (OSError, json.JSONDecodeError, KeyError,
            TypeError, ValueError) as error:
        raise RuntimeError(
            f'Cannot load DC motor configuration {path}: {error}') from error

    limits = (min_linear, max_linear, min_angular, max_angular)
    if not all(math.isfinite(limit) and limit > 0.0 for limit in limits):
        raise ValueError('DC motor speed limits must be positive and finite')
    if min_linear > max_linear or min_angular > max_angular:
        raise ValueError('DC motor minimum speeds cannot exceed maximums')
    return limits
