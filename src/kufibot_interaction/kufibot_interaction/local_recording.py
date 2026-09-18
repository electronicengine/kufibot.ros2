"""Two-channel, session-scoped recordings for the local voice agent."""
import audioop
import json
import os
from pathlib import Path
import threading
import time
import uuid
import wave


SAMPLE_RATE = 16000


def recording_root():
    return Path(os.environ.get('KUFIBOT_RECORDING_ROOT',
        '~/.local/share/kufibot/recordings')).expanduser()


class SessionRecording:
    """Writes user and robot PCM independently, then publishes one stereo WAV.

    Left channel is the user microphone; right channel is the robot's TTS.
    Timing is preserved by filling gaps from a shared monotonic session clock.
    """
    def __init__(self, root=None):
        self.root = Path(root).expanduser() if root else recording_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:10]
        self.started_at, self.started_mono = time.time(), time.monotonic()
        self._lock, self._frames = threading.Lock(), {'user': 0, 'assistant': 0}
        self._states = {'assistant': None}
        self._paths = {role: self.root / f'.{self.id}-{role}.wav' for role in self._frames}
        self._files = {role: wave.open(str(path), 'wb') for role, path in self._paths.items()}
        for stream in self._files.values():
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(SAMPLE_RATE)

    def write(self, role, pcm, sample_rate=SAMPLE_RATE):
        if role not in self._files or not pcm:
            return
        with self._lock:
            if sample_rate != SAMPLE_RATE:
                pcm, self._states[role] = audioop.ratecv(pcm, 2, 1, sample_rate,
                                                         SAMPLE_RATE, self._states[role])
            target = round((time.monotonic() - self.started_mono) * SAMPLE_RATE)
            if target > self._frames[role]:
                self._files[role].writeframesraw(b'\0\0' * (target - self._frames[role]))
                self._frames[role] = target
            self._files[role].writeframesraw(pcm)
            self._frames[role] += len(pcm) // 2

    def close(self):
        with self._lock:
            for stream in self._files.values():
                stream.close()
            output = self.root / f'{self.id}.wav'
            readers = {role: wave.open(str(path), 'rb') for role, path in self._paths.items()}
            try:
                with wave.open(str(output), 'wb') as merged:
                    merged.setnchannels(2); merged.setsampwidth(2); merged.setframerate(SAMPLE_RATE)
                    while True:
                        user = readers['user'].readframes(4096)
                        robot = readers['assistant'].readframes(4096)
                        size = max(len(user), len(robot)) // 2
                        if not size:
                            break
                        user = user.ljust(size * 2, b'\0')
                        robot = robot.ljust(size * 2, b'\0')
                        merged.writeframesraw(b''.join(user[i:i + 2] + robot[i:i + 2]
                            for i in range(0, size * 2, 2)))
            finally:
                for stream in readers.values(): stream.close()
                for path in self._paths.values(): path.unlink(missing_ok=True)
            info = {'id': self.id, 'created_at': self.started_at, 'duration_sec': max(self._frames.values()) / SAMPLE_RATE,
                    'channels': {'left': 'user', 'right': 'assistant'}, 'file': output.name}
            (self.root / f'{self.id}.json').write_text(json.dumps(info, ensure_ascii=False))
            return info
