"""Low-latency ALSA tracks used by the Verasist WebRTC session."""

import asyncio
import fractions
import sys
import time

import numpy as np

from aiortc import MediaStreamTrack
from av import AudioFrame

SAMPLE_RATE = 16000
FRAME_SAMPLES = 160


def audio_command(capture, device, rate, channels):
    """A pulse: device uses WSLg audio without requiring an ALSA plugin."""
    if device.startswith('pulse:'):
        command = ['parec' if capture else 'pacat', '--raw', '--format=s16le',
                   f'--rate={rate}', f'--channels={channels}', '--latency-msec=20']
        source = device.partition(':')[2]
        if source and source != 'default':
            command.append(f'--device={source}')
        return command
    command = ['arecord' if capture else 'aplay', '-D', device,
               '-f', 'S16_LE', '-r', str(rate), '-c', str(channels), '-t', 'raw']
    if capture:
        command.extend(['--buffer-time=200000', '--period-time=20000'])
    return command


class AlsaMicTrack(MediaStreamTrack):
    kind = 'audio'

    def __init__(self, device, processing=None, mute_during_playback=True,
                 noise_gate_rms=450.0, noise_gate_hangover_sec=0.5):
        super().__init__()
        self.device = device
        self.processing = processing
        self.mute_during_playback = bool(mute_during_playback)
        self.noise_gate_rms = max(0.0, float(noise_gate_rms))
        self.noise_gate_hangover = max(
            0.0, float(noise_gate_hangover_sec))
        self.gate_open_until = 0.0
        self.bot_speaking = False
        self.queue = asyncio.Queue(maxsize=100)
        self.started_at = None
        self.samples_sent = 0
        self.task = None
        self.stderr_task = None
        self.process = None
        self.capture_ready = asyncio.Event()
        self.capture_error = None
        self.frame_count = 0
        self.clipped_frames = 0

    async def start_capture(self):
        self.process = await asyncio.create_subprocess_exec(
            *audio_command(True, self.device, SAMPLE_RATE, 1),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.task = asyncio.create_task(self._reader())
        self.stderr_task = asyncio.create_task(self._log_stderr())
        try:
            await asyncio.wait_for(self.capture_ready.wait(), timeout=5.0)
            if self.capture_error:
                raise RuntimeError(self.capture_error)
        except BaseException:
            await self.close_capture()
            raise

    async def _log_stderr(self):
        async for line in self.process.stderr:
            print(f'[microphone] {line.decode(errors="replace").rstrip()}',
                  file=sys.stderr)

    async def _reader(self):
        try:
            while True:
                pcm = await self.process.stdout.readexactly(FRAME_SAMPLES * 2)
                self.capture_ready.set()
                self.frame_count += 1
                samples = np.frombuffer(pcm, dtype=np.int16)
                rms = float(np.sqrt(np.mean(
                    samples.astype(np.float32) ** 2))) if samples.size else 0.0
                if samples.size and np.max(np.abs(samples.astype(np.int32))) >= 32760:
                    self.clipped_frames += 1
                now = time.monotonic()
                if rms >= self.noise_gate_rms:
                    self.gate_open_until = now + self.noise_gate_hangover
                gate_closed = (
                    self.noise_gate_rms > 0.0
                    and now >= self.gate_open_until)
                playback_muted = (
                    self.bot_speaking and self.mute_during_playback)
                if self.frame_count % 500 == 0:
                    print(
                        f'[mic] frames={self.frame_count} rms={int(rms)} '
                        f'clipped={self.clipped_frames} queue={self.queue.qsize()} '
                        f'muted={playback_muted} gated={gate_closed}',
                        file=sys.stderr)
                # Optional half-duplex mode. It prevents speaker echo but
                # intentionally creates silence in the remote recording.
                if playback_muted or gate_closed:
                    pcm = b'\x00' * len(pcm)
                await self.queue.put(pcm)
        except asyncio.IncompleteReadError:
            self.capture_error = f'Microphone capture ended: {self.device}'
            self.capture_ready.set()
        except asyncio.CancelledError:
            return

    async def recv(self):
        if self.started_at is None:
            self.started_at = time.monotonic()
        pcm = await self.queue.get()
        frame = AudioFrame(format='s16', layout='mono', samples=FRAME_SAMPLES)
        frame.planes[0].update(pcm)
        frame.sample_rate = SAMPLE_RATE
        # RTP timestamps must be continuous. Wall-clock timestamps introduce
        # gaps whenever the asyncio task is scheduled a little late.
        frame.pts = self.samples_sent
        self.samples_sent += FRAME_SAMPLES
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        return frame

    async def close_capture(self):
        if self.task:
            self.task.cancel()
        if self.stderr_task:
            self.stderr_task.cancel()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        super().stop()


class AlsaSpeaker:
    """Consume assistant frames with a small jitter prebuffer."""

    def __init__(self, track, device, mic, state_callback=None):
        self.track = track
        self.device = device
        self.mic = mic
        self.state_callback = state_callback
        self.task = asyncio.create_task(self._run())
        self.process = None
        self.speaking = False
        self.last_voice_at = 0.0

    def _update_speaking(self, pcm):
        """Detect actual assistant speech; inbound WebRTC tracks stay open."""
        samples = np.frombuffer(pcm, dtype=np.int16)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) \
            if samples.size else 0.0
        now = time.monotonic()
        if rms >= 180.0:
            self.last_voice_at = now
        active = rms >= 180.0 or now - self.last_voice_at < 0.35
        if active != self.speaking:
            self.speaking = active
            self.mic.bot_speaking = active
            if self.state_callback:
                self.state_callback(active)

    async def _run(self):
        try:
            frames = [await self.track.recv()]
            for _ in range(7):
                frames.append(await self.track.recv())
            first = frames[0]
            self.process = await asyncio.create_subprocess_exec(
                *audio_command(False, self.device, first.sample_rate,
                               len(first.layout.channels)),
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            while True:
                for frame in frames:
                    if frame.format.name == 's16' and len(frame.layout.channels) > 1:
                        pcm = np.ascontiguousarray(frame.to_ndarray().T).tobytes()
                    else:
                        pcm = bytes(frame.planes[0])
                    self._update_speaking(pcm)
                    self.process.stdin.write(pcm)
                await self.process.stdin.drain()
                frames = [await self.track.recv()]
        except asyncio.CancelledError:
            pass
        except Exception as error:
            print(f'[speaker] Playback failed ({self.device}): {error}', file=sys.stderr)
        finally:
            if self.speaking:
                self.speaking = False
                self.mic.bot_speaking = False
                if self.state_callback:
                    self.state_callback(False)
            if self.process and self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()

    async def close(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
