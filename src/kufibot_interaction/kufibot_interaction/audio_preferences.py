"""Persistent selections of the robot's physical audio endpoints."""
import json
import os
from pathlib import Path
import re
import tempfile


def settings_path():
    return Path(os.environ.get('KUFIBOT_DEVICE_SETTINGS',
                               Path.home() / '.config/kufibot/devices.json'))


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Ses ayarları bir nesne olmalı')
    result = {}
    for key in ('microphone', 'speaker'):
        device = value.get(key, '')
        if not isinstance(device, str) or len(device) > 512:
            raise ValueError('Geçersiz ses aygıtı')
        if device and not re.fullmatch(r'pulse:[A-Za-z0-9_.:-]+', device):
            if key != 'speaker' or not re.fullmatch(r'bluetooth:(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', device):
                raise ValueError('Geçersiz ses aygıtı')
        if 'kufibot_aec_' in device or device.endswith('.monitor'):
            raise ValueError('Fiziksel bir ses aygıtı seçin')
        result[key] = device
    return result


def read_preferences():
    try:
        return validate(json.loads(settings_path().read_text()))
    except FileNotFoundError:
        return {'microphone': '', 'speaker': ''}


def save_preferences(value):
    value = validate(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.devices-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'schema_version': 1, **value}, stream)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return value


def bluetooth_prefix(device):
    return 'bluez_output.' + device.removeprefix('bluetooth:').replace(':', '_')


def resolve_selection(device, devices):
    if not device:
        return None
    if device.startswith('bluetooth:'):
        prefix = bluetooth_prefix(device)
        match = next((item['name'] for item in devices if item['name'].startswith(prefix + '.')
                      or item['name'] == prefix), None)
    else:
        match = next((item['name'] for item in devices if 'pulse:' + item['name'] == device), None)
    if match is None:
        raise RuntimeError('Seçilen ses aygıtı bağlı değil. Bağlantı Ayarları’nı kontrol edin.')
    return 'pulse:' + match
