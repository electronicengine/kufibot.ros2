"""Shared validation for deterministic robot actions (no inference or ROS)."""
import math
import threading
import time


def validate_tool_arguments(name, params, *, min_speed=None, max_speed=None):
    if not isinstance(params, dict):
        raise ValueError('Araç parametreleri nesne olmalı')
    if name == 'play_motion':
        from .mimics import validate
        validate(params['motion'])
    elif name == 'move_base':
        if params.get('direction') not in ('forward', 'backward'):
            raise ValueError('Sürüş yönü forward veya backward olmalı')
        speed, duration = params.get('speed_mps'), params.get('duration_sec', 1)
        if type(speed) not in (int, float) or not math.isfinite(speed) or speed <= 0:
            raise ValueError('Sürüş hızı pozitif ve sonlu olmalı')
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 5:
            raise ValueError('Sürüş süresi 0–5 saniye olmalı (0 hariç)')
        if min_speed is not None and not min_speed <= speed <= max_speed:
            raise ValueError(f'Sürüş hızı {min_speed}–{max_speed} m/s arasında olmalı')


def perception_snapshot(cache, max_age):
    return {name: cache.get(name, max_age) for name in ('faces', 'hands', 'objects')}


class TimedCache:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def set(self, name, value, *, age_sec=0):
        with self.lock:
            self.values[name] = (time.monotonic()-age_sec, value)

    def get(self, name, max_age):
        with self.lock:
            item = self.values.get(name)
        if item is None:
            return {'status': 'unavailable'}
        age = time.monotonic()-item[0]
        return {'status': 'ok' if age <= max_age else 'stale',
                'age_sec': round(age, 2), 'value': item[1]}
