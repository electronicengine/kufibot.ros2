"""Config-driven expression catalogue compatible with Kufibot C++ configs."""

import json
import re
from pathlib import Path


SUPPORTED_JOINTS = {
    'rightArm', 'leftArm', 'neck', 'headLeftRight', 'eyeLeft', 'eyeRight'
}


class ExpressionConfigError(RuntimeError):
    pass


class ExpressionLibrary:
    """Load gesture metadata, symbolic joint angles and motion sequences."""

    def __init__(self, gesture_path, motion_path, joint_path):
        gestures = self._read(gesture_path)
        motions_doc = self._read(motion_path)
        joint_doc = self._read(joint_path)
        self.angles = {
            joint: {state: float(value['angle'])
                    for state, value in states.items()}
            for joint, states in joint_doc.items()
            if joint in SUPPORTED_JOINTS
        }
        self.idle = self._resolve(
            motions_doc.get('default_idle', {}).get('joints', {}))
        self.motions = {}
        for category in ('emotional_gestures', 'reactional_gestures'):
            for name, motion in motions_doc.get('motions', {}).get(
                    category, {}).items():
                self.motions[name] = self._compile_motion(name, motion)
        self.descriptions = {}
        for category in ('emotional_gestures', 'reactional_gestures'):
            for item in gestures.get(category, []):
                self.descriptions[item['type']] = item.get('description', '')
        if not self.motions:
            raise ExpressionConfigError('No emotional/reactional motions found')

    @staticmethod
    def _read(path):
        try:
            with Path(path).expanduser().open(encoding='utf-8') as stream:
                return json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise ExpressionConfigError(f'Cannot load {path}: {error}') from error

    def _resolve(self, symbolic):
        resolved = {}
        for joint, state in symbolic.items():
            if joint not in SUPPORTED_JOINTS:
                continue
            try:
                resolved[joint] = self.angles[joint][state]
            except KeyError as error:
                raise ExpressionConfigError(
                    f'Unknown joint state {joint}.{state}') from error
        return resolved

    def _compile_motion(self, name, motion):
        duration = max(0, int(motion.get('duration', 0)))
        events = [(0, self._resolve(motion.get('joints', {})))]
        for item in motion.get('sequence', []):
            at_ms = max(0, min(duration, int(item.get('time', 0))))
            events.append((at_ms, self._resolve(item.get('joints', {}))))
        events.sort(key=lambda event: event[0])
        return {'name': name, 'duration_ms': duration, 'events': events,
                'description': motion.get('description', '')}

    def classify(self, sentence):
        """Choose the closest configured motion for Turkish/English text."""
        text = sentence.casefold()
        cues = {
            'greeting': ('merhaba', 'selam', 'günaydın', 'hoş geld', 'hello'),
            'angry': ('kızgın', 'sinirli', 'öfke', 'kabul edilemez', 'angry'),
            'funny': ('şaka', 'komik', 'güldüm', 'haha', 'funny'),
            'worried': ('endişe', 'kaygı', 'üzgün', 'maalesef', 'dikkat', 'worried'),
            'surprised': ('şaşır', 'vay', 'inanılmaz', 'surpris'),
            'curious': ('merak', 'acaba', 'neden', 'nasıl', 'curious'),
            'confident': ('eminim', 'kesinlikle', 'başarabilir', 'confident'),
            'accepting': ('kabul', 'olur', 'tabii', 'elbette', 'accept'),
            'rejecting': ('hayır', 'redded', 'yapamam', 'uygun değil', 'reject'),
            'thinking': ('düşün', 'bakalım', 'inceleyelim', 'emin değil', 'think'),
            'agreeing': ('katılıyorum', 'haklısın', 'doğru', 'aynen', 'agree'),
            'happy': ('mutlu', 'sevindim', 'harika', 'süper', 'tebrik', 'happy'),
            'serious': ('ciddi', 'önemli', 'risk', 'uyarı', 'serious'),
        }
        best = None
        best_score = 0
        for name, words in cues.items():
            if name not in self.motions:
                continue
            score = sum(1 for word in words if word in text)
            if score > best_score:
                best, best_score = name, score
        if best:
            return best
        return 'talking' if 'talking' in self.motions else next(iter(self.motions))

    @staticmethod
    def sentences(text):
        return [part.strip() for part in re.split(r'(?<=[.!?])\s+', text)
                if part.strip()]
