"""Two-channel, session-scoped recordings for the local voice agent."""
from array import array
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
    def __init__(self, root=None, workflow=None):
        self.root = Path(root).expanduser() if root else recording_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:10]
        self.started_at, self.started_mono = time.time(), time.monotonic()
        self._lock, self._frames = threading.Lock(), {'user': 0, 'assistant': 0}
        self._states = {'assistant': None}
        workflow = workflow or {}
        self.workflow = {'id': workflow.get('id'), 'name': workflow.get('name', 'Yerel görüşme'),
                         'nodes': {node['id']: node.get('data', {}).get('name', node['id'])
                                   for node in workflow.get('nodes', [])}}
        self.events = []
        self._partial_at = {}
        self._utterances = {}
        self._closed = False
        self._info = None
        self._paths = {role: self.root / f'.{self.id}-{role}.wav' for role in self._frames}
        self._files = {role: wave.open(str(path), 'wb') for role, path in self._paths.items()}
        for stream in self._files.values():
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(SAMPLE_RATE)

    def begin_utterance(self, role):
        with self._lock:
            self._partial_at.pop(role, None)
            self._utterances[role] = {'offset_ms': None, 'event': None}

    def write(self, role, pcm, sample_rate=SAMPLE_RATE):
        if role not in self._files or not pcm:
            return
        with self._lock:
            if self._closed:
                return
            if sample_rate != SAMPLE_RATE:
                pcm, self._states[role] = audioop.ratecv(pcm, 2, 1, sample_rate,
                                                         SAMPLE_RATE, self._states[role])
            target = round((time.monotonic() - self.started_mono) * SAMPLE_RATE)
            utterance = self._utterances.get(role)
            # User PCM is contiguous within an utterance, even when VAD/STT
            # processing delivers chunks late. Align its first chunk only;
            # padding every chunk inserts artificial gaps into the recording.
            continuing_user = (role == 'user' and utterance is not None
                               and utterance['offset_ms'] is not None)
            if not continuing_user and target > self._frames[role]:
                self._files[role].writeframesraw(b'\0\0' * (target - self._frames[role]))
                self._frames[role] = target
            if utterance is not None and utterance['offset_ms'] is None:
                utterance['offset_ms'] = round(self._frames[role] / SAMPLE_RATE * 1000)
                # Short TTS can start after its final transcript was emitted.
                if utterance['event'] is not None:
                    utterance['event']['offset_ms'] = utterance['offset_ms']
            self._files[role].writeframesraw(pcm)
            self._frames[role] += len(pcm) // 2

    def record_event(self, kind, **values):
        """Persist final messages and engine activity on the audio session clock."""
        with self._lock:
            if self._closed:
                return
            offset = max(0, round((time.monotonic() - self.started_mono) * 1000))
            if kind == 'transcript':
                role = values.get('role', 'user')
                if values.get('final') is False:
                    if values.get('text'):
                        self._partial_at.setdefault(role, offset)
                    return
                offset = self._partial_at.pop(role, offset)
                utterance = self._utterances.get(role)
                if utterance is not None and utterance['offset_ms'] is not None:
                    offset = utterance['offset_ms']
                if not values.get('text'):
                    return
                event = {'type': 'transcript', 'role': role, 'text': values['text']}
            elif kind == 'workflow_event':
                event = values.get('event', {})
                if event.get('type') not in ('node', 'transition', 'tool_start', 'tool_result', 'semantic_match'):
                    return
            else:
                return
            # Copy provider payloads: later mutation must not rewrite the archive.
            event = json.loads(json.dumps(event, ensure_ascii=False))
            stored = {**event, 'offset_ms': offset, 'sequence': len(self.events)}
            self.events.append(stored)
            if kind == 'transcript' and utterance is not None:
                utterance['event'] = stored

    def close(self):
        with self._lock:
            if self._info is not None:
                return self._info
            self._closed = True
            frames = max(round((time.monotonic() - self.started_mono) * SAMPLE_RATE), *self._frames.values())
            for role, stream in self._files.items():
                remaining = frames - self._frames[role]
                while remaining:
                    size = min(remaining, 16000)
                    stream.writeframesraw(b'\0\0' * size)
                    remaining -= size
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
                        stereo = bytearray(size * 4)
                        stereo[0::4], stereo[1::4] = user[0::2], user[1::2]
                        stereo[2::4], stereo[3::4] = robot[0::2], robot[1::2]
                        merged.writeframesraw(stereo)
            finally:
                for stream in readers.values(): stream.close()
                for path in self._paths.values(): path.unlink(missing_ok=True)
            info = {'id': self.id, 'created_at': self.started_at, 'duration_sec': frames / SAMPLE_RATE,
                    'channels': {'left': 'user', 'right': 'assistant'}, 'file': output.name,
                    'schema_version': 2, 'workflow': self.workflow,
                    'events': sorted(self.events, key=lambda event: (event['offset_ms'], event['sequence']))}
            temporary = self.root / f'.{self.id}.json.tmp'
            temporary.write_text(json.dumps(info, ensure_ascii=False))
            temporary.replace(self.root / f'{self.id}.json')
            self._info = info
            return info


def recording_waveform(path, max_bins=1200):
    """Bounded stereo peaks, streamed in small blocks without decoding in the browser."""
    import sys
    with wave.open(str(path), 'rb') as sound:
        if sound.getnchannels() != 2 or sound.getsampwidth() != 2:
            raise ValueError('Desteklenmeyen ses biçimi')
        frames, rate = sound.getnframes(), sound.getframerate()
        bin_frames = max(1, (frames + max_bins - 1) // max_bins)
        channels = {'user': [], 'assistant': []}
        remaining = frames
        while remaining:
            count = min(bin_frames, remaining)
            low, high = [32767, 32767], [-32768, -32768]
            unread = count
            while unread:
                size = min(unread, 4096)
                samples = array('h', sound.readframes(size))
                if sys.byteorder != 'little':
                    samples.byteswap()
                if len(samples) != size * 2:
                    raise ValueError('Eksik ses verisi')
                for channel in (0, 1):
                    values = samples[channel::2]
                    low[channel] = min(low[channel], min(values))
                    high[channel] = max(high[channel], max(values))
                unread -= size
            for channel, role in enumerate(channels):
                channels[role].append([round(low[channel] / 32768, 4), round(high[channel] / 32768, 4)])
            remaining -= count
        return {'duration_sec': frames / rate, 'channels': channels}
