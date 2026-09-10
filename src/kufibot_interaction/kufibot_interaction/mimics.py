"""Versioned robot-local keyframes and deterministic servo-space evaluation."""
import copy
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

from .joint_limits import JOINT_LIMITS, NEUTRAL_ANGLES


class RevisionConflict(ValueError):
    pass


def evaluate(motion, elapsed_ms):
    frames = motion['keyframes']
    previous = frames[0]
    for following in frames[1:]:
        if elapsed_ms < following['time_ms']:
            if motion['interpolation'] == 'step':
                return dict(previous['joints'])
            fraction = max(0, (elapsed_ms-previous['time_ms']) /
                           (following['time_ms']-previous['time_ms']))
            return {joint: angle + (following['joints'][joint]-angle)*fraction
                    for joint, angle in previous['joints'].items()}
        previous = following
    return dict(previous['joints'])


def validate(document):
    if not isinstance(document, dict):
        raise ValueError('Mimik nesne olmalı')
    value = copy.deepcopy(document)
    if not isinstance(value.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value['id']):
        raise ValueError('Geçersiz mimik kimliği')
    for key, limit in [('name', 100), ('description', 2000)]:
        if not isinstance(value.get(key), str) or len(value[key]) > limit:
            raise ValueError('Geçersiz ad veya açıklama')
    if not value['name'].strip():
        raise ValueError('Mimik adı gerekli')
    if type(value.get('revision')) is not int or value['revision'] < 0:
        raise ValueError('Geçersiz sürüm')
    if value.get('interpolation') not in ('linear', 'step'):
        raise ValueError('Geçersiz geçiş türü')
    duration = value.get('duration_ms')
    if type(duration) is not int or not 0 <= duration <= 300000:
        raise ValueError('Süre 0–300 saniye olmalı')
    frames = value.get('keyframes')
    if not isinstance(frames, list) or not 1 <= len(frames) <= 1000:
        raise ValueError('1–1000 zaman adımı gerekli')
    last = -1
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError('Geçersiz zaman adımı')
        at = frame.get('time_ms')
        if type(at) is not int or not last < at <= duration:
            raise ValueError('Zamanlar benzersiz, artan ve süre içinde olmalı')
        joints = frame.get('joints')
        if not isinstance(joints, dict) or set(joints) != set(JOINT_LIMITS):
            raise ValueError('Her adım altı servo açısını içermeli')
        for name, angle in joints.items():
            low, high = JOINT_LIMITS[name]
            if type(angle) not in (float, int) or not math.isfinite(angle) or not low <= angle <= high:
                raise ValueError(f'{name}: açı {low}–{high} arasında olmalı')
        last = at
    if frames[0]['time_ms'] != 0:
        raise ValueError('İlk zaman adımı sıfır olmalı')
    return {key: value[key] for key in ('id', 'name', 'description', 'revision',
                                      'duration_ms', 'interpolation', 'keyframes')}


def from_library(library):
    result = {}
    for name, motion in getattr(library, '_base_motions', library.motions).items():
        pose = {**NEUTRAL_ANGLES, **library.idle}
        frames = {}
        for at, changes in motion['events']:
            pose.update(changes)
            frames[at] = {'time_ms': at, 'joints': dict(pose)}
        result[name] = dict(id=name, name=name, description=library.descriptions.get(name, ''),
                            revision=0, duration_ms=motion['duration_ms'], interpolation='step',
                            keyframes=list(frames.values()))
    return result


class MimicStore:
    def __init__(self, path=None, defaults=None):
        self.path = Path(path or os.environ.get('KUFIBOT_MIMICS_FILE',
                         '~/.local/share/kufibot/mimics.json')).expanduser()
        self.defaults = defaults or {}
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        with self.path.open(encoding='utf-8') as stream:
            document = json.load(stream)
        if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('mimics'), list):
            raise ValueError('Desteklenmeyen mimik kitaplığı')
        records = [validate(item) for item in document['mimics']]
        if len({item['id'] for item in records}) != len(records):
            raise ValueError('Tekrarlanan mimik kimliği')
        return {item['id']: item for item in records}

    def all(self):
        with self.lock:
            return copy.deepcopy({**self.defaults, **self._read()})

    def get(self, name):
        try:
            return self.all()[name]
        except KeyError as error:
            raise ValueError('Mimik bulunamadı') from error

    @contextmanager
    def _writer_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, document):
        value = validate(document)
        with self.lock, self._writer_lock():
            records = self._read()
            existing = records.get(value['id'], self.defaults.get(value['id']))
            if value['revision'] != (existing['revision'] if existing else 0):
                raise RevisionConflict('Mimik başka bir istemcide değişti; yeniden yükleyin')
            value['revision'] += 1
            records[value['id']] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.mimics-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'schema_version': 1, 'mimics': list(records.values())}, stream,
                              ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return copy.deepcopy(value)


def default_store(gesture_path=None, motion_path=None, joint_path=None):
    from .expression_engine import ExpressionLibrary
    root = Path(__file__).with_name('expression_defaults')
    physical = Path('/home/kufi/workspace/kufibot.cpp/config')
    if all((physical/name).is_file() for name in ('gesture_config.json','motion_definitions.json','joint_angles.json')):
        root = physical
    library = ExpressionLibrary(gesture_path or root/'gesture_config.json',
                                motion_path or root/'motion_definitions.json',
                                joint_path or root/'joint_angles.json', load_users=False)
    return MimicStore(defaults=from_library(library))
