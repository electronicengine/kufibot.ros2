"""Small, ROS-independent voice timing, VAD and resource lease helpers."""
from collections import deque
from pathlib import Path
import json
import math
import resource
import multiprocessing
import os
import time
import uuid

PHASES = {'loading', 'listening', 'transcribing', 'thinking', 'synthesizing', 'speaking', 'idle'}
# Face/hand tracking competes with llama.cpp for CPU.  The worker keeps the
# ``thinking`` phase until the streamed LLM response is completely finished,
# including while early sentences are being spoken.  Other phases must leave
# tracking available so the robot can reconnect with the person immediately.
BUSY_PHASES = {'thinking'}
DEFAULTS = dict(vad_model='/usr/local/ai.models/vad/silero_vad.onnx',
                vad_threshold=0.5, vad_pre_roll_ms=300, vad_silence_ms=600,
                vad_min_speech_ms=96, max_utterance_sec=30.0,
                llm_threads=3, llm_batch_threads=4, llm_context=2048,
                llm_max_tokens=160, tts_threads=1, hailo_timeout_sec=30.0)


def validate_runtime(config):
    result = {**DEFAULTS, **config}
    for name in DEFAULTS:
        if name == 'vad_model':
            continue
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'Invalid local voice parameter: {name}')
    if not 0 < result['vad_threshold'] < 1:
        raise ValueError('vad_threshold must be between zero and one')
    for name in ('llm_threads', 'llm_batch_threads', 'llm_context', 'llm_max_tokens', 'tts_threads'):
        if not isinstance(result[name], int):
            raise ValueError(f'{name} must be an integer')
    if result['max_utterance_sec'] > 120:
        raise ValueError('max_utterance_sec cannot exceed the 120-second buffer bound')
    if result['llm_context'] <= result['llm_max_tokens'] + 64:
        raise ValueError('LLM context must leave space for the prompt')
    return result


class PhaseLease:
    """Expired publishers cannot leave consumers paused. Uses receiver-local time."""
    def __init__(self, clock=time.monotonic, ttl=3.0):
        self.clock, self.ttl = clock, ttl
        self.phase, self.at = 'idle', float('-inf')

    def receive(self, payload):
        try:
            phase = json.loads(payload)['phase']
            if phase not in PHASES:
                return
        except (ValueError, KeyError, TypeError):
            return
        self.phase, self.at = phase, self.clock()

    @property
    def busy(self):
        return self.phase in BUSY_PHASES and self.clock() - self.at < self.ttl


def attach_phase_lease(node, callback):
    """Attach a transient heartbeat subscription without importing ROS in tests."""
    from std_msgs.msg import String
    from rclpy.qos import QoSProfile, DurabilityPolicy
    lease = PhaseLease()
    previous = [False]

    def update(msg=None):
        if msg is not None:
            lease.receive(msg.data)
        active = lease.busy
        if active != previous[0]:
            previous[0] = active
            callback(active)

    node.create_subscription(String, 'local_ai/phase', update,
                             QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
    node.create_timer(0.25, update)
    return lease


class Reporter:
    """A tiny process keeps the lease alive even when native loading holds the GIL.

    Created before any inference libraries/threads are loaded. The worker's emit
    callback uses an inherited process lock so heartbeat JSON cannot interleave.
    """
    def __init__(self, notify):
        self.notify = notify
        self.owner_pid = os.getpid()
        context = multiprocessing.get_context('fork')
        self.phase = context.Array('c', 32, lock=False)
        self.phase.value = b'idle'
        self.turn = 0
        self.session = uuid.uuid4().hex
        self.lock = context.RLock()
        self.stopped = context.Event()
        self.heartbeat = context.Process(target=self._heartbeat, daemon=True)
        self.heartbeat.start()

    def set_phase(self, phase):
        with self.lock:
            self.phase.value = phase.encode()
            self.notify('phase', phase=phase, monotonic_sec=time.monotonic())

    def _heartbeat(self):
        # Do not keep stdout/the lease alive after the inference worker dies.
        # Linux also terminates this helper if the owner dies while holding a lock.
        import ctypes
        import signal
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            return
        if os.getppid() != self.owner_pid:
            return
        while not self.stopped.wait(0.75):
            if os.getppid() != self.owner_pid:
                return
            with self.lock:
                self.notify('phase', phase=self.phase.value.decode(), monotonic_sec=time.monotonic())

    def mark(self, name, at=None, **extra):
        usage = resource.getrusage(resource.RUSAGE_SELF)
        fields = dict(event=name, turn=self.turn, session=self.session, monotonic_sec=time.monotonic() if at is None else at,
                      cpu_sec=usage.ru_utime + usage.ru_stime, peak_rss_kb=usage.ru_maxrss)
        try:
            values = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
            fields.update(rss_kb=int(values['VmRSS'].split()[0]), swap_kb=int(values['VmSwap'].split()[0]))
            fields['temperature_c'] = int(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
        except (OSError, KeyError, ValueError):
            pass
        self.notify('metric', **fields, **extra)

    def close(self):
        self.stopped.set()
        self.heartbeat.join(timeout=2)
        if self.heartbeat.is_alive():
            self.heartbeat.terminate()
            self.heartbeat.join(timeout=2)
        self.set_phase('idle')


class SileroVad:
    """Silero v5/v6 ONNX recurrent state, 512 samples plus 64-sample context."""
    def __init__(self, path):
        import numpy as np
        import onnxruntime as ort
        if not Path(path).is_file():
            raise ValueError(f'VAD model missing: {path}; run tools/local_voice/prepare_assets.py')
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
        self.np = np
        self.reset()

    def reset(self):
        self.state = self.np.zeros((2, 1, 128), dtype=self.np.float32)
        self.context = self.np.zeros((1, 64), dtype=self.np.float32)

    def __call__(self, pcm):
        np = self.np
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32)[None, :] / 32768
        if samples.shape[1] != 512:
            raise ValueError('VAD requires exactly 512 samples')
        audio = np.concatenate((self.context, samples), axis=1)
        probability, self.state = self.session.run(None, dict(input=audio, state=self.state, sr=np.array(16000, dtype=np.int64)))
        self.context = samples[:, -64:]
        return float(probability.item())


class SpeechGate:
    """Bounded pre-roll, hysteresis and endpointing; no inference on pure silence."""
    def __init__(self, config):
        self.config = config
        self.pre = deque(maxlen=math.ceil(config['vad_pre_roll_ms'] / 32) + 1)
        self.active = False
        self.voiced = self.silent = self.frames = 0
        self.last_voice = None

    def push(self, pcm, probability, at):
        speech = probability >= self.config['vad_threshold']
        if self.active and probability >= max(0.01, self.config['vad_threshold'] - 0.15):
            speech = True
        if not self.active:
            self.pre.append(pcm)
            self.voiced = self.voiced + 1 if speech else 0
            if self.voiced * 32 < self.config['vad_min_speech_ms']:
                return [], False
            self.active = True
            self.last_voice = at
            self.frames = len(self.pre)
            frames = list(self.pre)
            self.pre.clear()
            return frames, False
        self.frames += 1
        if speech:
            self.last_voice = at
            self.silent = 0
        else:
            self.silent += 1
        done = (self.silent * 32 >= self.config['vad_silence_ms'] or
                self.frames * 0.032 >= self.config['max_utterance_sec'])
        return [pcm], done
