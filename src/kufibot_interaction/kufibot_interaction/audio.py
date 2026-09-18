"""Low-latency ALSA tracks used by the Verasist WebRTC session."""

import asyncio
import fractions
import json
import math
import sys
import time

import numpy as np

from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame, AudioResampler

SAMPLE_RATE = 16000
FRAME_SAMPLES = 160


def audio_command(capture, device, rate, channels):
    """A pulse: device uses WSLg audio without requiring an ALSA plugin."""
    if device == 'pulse' or device.startswith('pulse:'):
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

    def __init__(self, device, processing=None, mute_during_playback=False,
                 noise_gate_rms=450.0, noise_gate_hangover_sec=0.5,
                 noise_suppression_db=0.0, barge_in_rms=0.0,
                 barge_in_start_sec=0.18, playback_echo_tail_sec=1.2):
        super().__init__()
        self.device = device
        self.processing = processing
        self.noise_suppression_db = float(noise_suppression_db)
        self.denoiser = None
        self.mute_during_playback = bool(mute_during_playback)
        self.noise_gate_rms = max(0.0, float(noise_gate_rms))
        self.noise_gate_hangover = max(
            0.0, float(noise_gate_hangover_sec))
        self.gate_open_until = 0.0
        self.bot_speaking = False
        if not math.isfinite(playback_echo_tail_sec) or not 0 <= playback_echo_tail_sec <= 5:
            raise ValueError('Playback echo tail must be between 0 and 5 seconds')
        self.playback_echo_tail = playback_echo_tail_sec
        self.playback_protected_until = 0.0
        self.barge_in_guard = None
        if barge_in_rms != 0.0:
            from .audio_processing import PlaybackBargeInGuard
            self.barge_in_guard = PlaybackBargeInGuard(barge_in_rms, barge_in_start_sec)
        self.queue = asyncio.Queue(maxsize=20)
        self.started_at = None
        self.samples_sent = 0
        self.task = None
        self.stderr_task = None
        self.process = None
        self.capture_ready = asyncio.Event()
        self.capture_error = None
        self.suspended = False
        self.frame_count = 0
        self.clipped_frames = 0

    def protect_playback_until(self, playback_end):
        # Local PCM timing, rather than the server's stopped-speaking event,
        # determines when buffered playback plus its acoustic tail can end.
        self.playback_protected_until = max(
            self.playback_protected_until, playback_end + self.playback_echo_tail)

    async def start_capture(self):
        if self.noise_suppression_db != 0.0:
            from .audio_processing import SpeexDenoiser
            self.denoiser = SpeexDenoiser(
                SAMPLE_RATE, FRAME_SAMPLES, self.noise_suppression_db)
        try:
            await self._start_capture()
        except BaseException:
            await self.close_capture()
            raise

    async def _start_capture(self):
        self.process = await asyncio.create_subprocess_exec(
            *audio_command(True, self.device, SAMPLE_RATE, 1),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.task = asyncio.create_task(self._reader())
        self.stderr_task = asyncio.create_task(self._log_stderr())
        await asyncio.wait_for(self.capture_ready.wait(), timeout=5.0)
        if self.capture_error:
            raise RuntimeError(self.capture_error)

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
                if self.denoiser:
                    pcm = self.denoiser.process(pcm)
                    samples = np.frombuffer(pcm, dtype=np.int16)
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                now = time.monotonic()
                if rms >= self.noise_gate_rms:
                    self.gate_open_until = now + self.noise_gate_hangover
                gate_closed = (
                    self.noise_gate_rms > 0.0
                    and now >= self.gate_open_until)
                playback_muted = (
                    (self.bot_speaking or now < self.playback_protected_until)
                    and self.mute_during_playback)
                if self.frame_count % 500 == 0:
                    print(
                        f'[mic] frames={self.frame_count} rms={int(rms)} '
                        f'clipped={self.clipped_frames} queue={self.queue.qsize()} '
                        f'muted={playback_muted} gated={gate_closed} '
                        f'barge_suppressed={self.barge_in_guard.suppressed_frames if self.barge_in_guard else 0}',
                        file=sys.stderr)
                # Optional half-duplex mode. It prevents speaker echo but
                # intentionally creates silence in the remote recording.
                if playback_muted or gate_closed:
                    pcm = b'\x00' * len(pcm)
                if self.barge_in_guard:
                    pcm = self.barge_in_guard.process(
                        pcm, rms, self.bot_speaking or now < self.playback_protected_until)
                    if pcm is None:
                        continue
                if self.queue.full():
                    self.queue.get_nowait()
                self.queue.put_nowait(pcm)
        except asyncio.CancelledError:
            return
        except Exception as error:
            self.capture_error = f'Microphone capture ended ({self.device}): {error}'
            self.capture_ready.set()
        finally:
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait(None)

    async def recv(self):
        if self.started_at is None:
            self.started_at = time.monotonic()
        if self.readyState == 'ended':
            raise MediaStreamError
        # A local USB disconnect must not end the RTP sender. Send paced
        # silence until capture is available again, preserving track and PTS.
        try:
            pcm = await asyncio.wait_for(
                self.queue.get(), FRAME_SAMPLES / SAMPLE_RATE)
        except asyncio.TimeoutError:
            pcm = None
        if self.readyState == 'ended':
            raise MediaStreamError
        if pcm is None or self.suspended:
            pcm = bytes(FRAME_SAMPLES * 2)
            deadline = self.started_at + self.samples_sent / SAMPLE_RATE
            await asyncio.sleep(max(0.0, deadline - time.monotonic()))
        frame = AudioFrame(format='s16', layout='mono', samples=FRAME_SAMPLES)
        frame.planes[0].update(pcm)
        frame.sample_rate = SAMPLE_RATE
        # RTP timestamps must be continuous. Wall-clock timestamps introduce
        # gaps whenever the asyncio task is scheduled a little late.
        frame.pts = self.samples_sent
        self.samples_sent += FRAME_SAMPLES
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        return frame

    async def restart_capture(self):
        """Reopen only the local recorder; keep the WebRTC track alive."""
        await self._close_recorder()
        while not self.queue.empty():
            self.queue.get_nowait()
        self.capture_error = None
        self.capture_ready.clear()
        try:
            await self._start_capture()
        except BaseException as error:
            self.capture_error = f'Microphone reconnect failed: {error}'
            await self._close_recorder()
            raise

    async def _close_recorder(self):
        tasks = [t for t in (self.task, self.stderr_task) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        self.task = self.stderr_task = self.process = None

    async def close_capture(self):
        await self._close_recorder()
        if self.denoiser:
            self.denoiser.close()
            self.denoiser = None
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(None)
        super().stop()


async def check_aec_devices(mic_device, speaker_device):
    """Check physical targets as well as virtual endpoints; never accept dummy audio."""
    if 'kufibot_aec_' not in mic_device + speaker_device:
        return
    if (mic_device, speaker_device) != ('pulse:kufibot_aec_source', 'pulse:kufibot_aec_sink'):
        raise RuntimeError('AEC requires both kufibot_aec_source and kufibot_aec_sink')
    async def devices(kind):
        process = await asyncio.create_subprocess_exec(
            'pactl', '--format=json', 'list', kind,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode(errors='replace'))
        return json.loads(stdout)
    sources, sinks = await asyncio.gather(devices('sources'), devices('sinks'))
    source_names = {v['name'] for v in sources}
    sink_names = {v['name'] for v in sinks}
    if not {'kufibot_aec_source', 'alsa_input.usb-Generic_HD_camera_20181212000000-02.mono-fallback'} <= source_names:
        raise RuntimeError('AEC camera source is unavailable')
    if 'kufibot_aec_sink' not in sink_names or not any(
            name.startswith('bluez_output.04_57_91_5A_8E_B7') for name in sink_names):
        raise RuntimeError('MI BT 18I Bluetooth speaker is disconnected; AEC unavailable')


class AlsaSpeaker:
    """Continuous consumption with bounded playback latency and explicit interruption."""

    def __init__(self, track, device, mic, state_callback=None):
        self.track, self.device, self.mic = track, device, mic
        self.state_callback = state_callback
        self.process = None
        self.speaking = False
        self.last_voice_at = 0.0
        self.playback_until = 0.0
        self.pending_voice_write = False
        self.generation = 0
        self.interrupted = False
        self.closing = False
        self.process_lock = asyncio.Lock()
        self.error = None
        self.suspended = False
        self.timer = asyncio.create_task(self._silence_watch())
        self.task = asyncio.create_task(self._run())

    def _set_speaking(self, active):
        if active != self.speaking:
            self.speaking = active
            if not active:
                # An interrupted or failed Bluetooth write may outlive the
                # original deadline. Keep a fresh tail when playback stops.
                self.mic.protect_playback_until(time.monotonic())
            self.mic.bot_speaking = active
            if self.state_callback:
                self.state_callback(active)

    def _update_speaking(self, pcm):
        samples = np.frombuffer(pcm, dtype=np.int16)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if samples.size else 0.0
        now = time.monotonic()
        self.playback_until = max(now, self.playback_until) + len(pcm) / (48000 * 2)
        voiced = rms >= 180.0
        if voiced:
            self.last_voice_at = now
            self.mic.protect_playback_until(self.playback_until)
            self._set_speaking(True)
        return voiced

    async def _silence_watch(self):
        while True:
            await asyncio.sleep(0.05)
            if (self.speaking and not self.pending_voice_write
                    and time.monotonic() - self.last_voice_at >= 0.35):
                self._set_speaking(False)

    async def _stop_player(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            await process.wait()
        self.playback_until = 0.0

    def interrupt(self):
        # Killing only this playback stream discards pacat/aplay's queued bytes.
        # The peer track must continue to be consumed for subsequent responses.
        self.generation += 1
        self.interrupted = True
        self._set_speaking(False)
        return self._interrupt_player()

    async def _interrupt_player(self):
        async with self.process_lock:
            await self._stop_player()

    def resume(self):
        self.interrupted = False

    async def restart_playback(self):
        """Reopen playback on the existing remote track without reconnecting."""
        await asyncio.gather(self.task, return_exceptions=True)
        if self.closing or self.track.readyState == 'ended':
            return
        self.error = None
        self.timer = asyncio.create_task(self._silence_watch())
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        resampler = AudioResampler(format='s16', layout='mono', rate=48000)
        try:
            while not self.closing:
                generation = self.generation
                frame = await self.track.recv()
                if generation != self.generation or self.interrupted or self.suspended:
                    resampler = AudioResampler(format='s16', layout='mono', rate=48000)
                    continue
                for converted in resampler.resample(frame):
                    pcm = converted.to_ndarray().tobytes()
                    async with self.process_lock:
                        if generation != self.generation or self.interrupted:
                            break
                        if self.process is None:
                            self.process = await asyncio.create_subprocess_exec(
                                *audio_command(False, self.device, 48000, 1),
                                stdin=asyncio.subprocess.PIPE,
                                stderr=None)
                        voiced = self._update_speaking(pcm)
                        player = self.process
                        player.stdin.write(pcm)
                    # Bluetooth activation may exceed 500 ms. Keep the wait
                    # outside the lock so interruption can still kill the stream
                    # immediately and discard queued PCM while drain is pending.
                    try:
                        self.pending_voice_write = voiced
                        await asyncio.wait_for(player.stdin.drain(), 5.0)
                        if voiced:
                            self.last_voice_at = time.monotonic()
                            self.mic.protect_playback_until(max(
                                self.last_voice_at, self.playback_until))
                    except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError):
                        if generation != self.generation:
                            break
                        raise
                    finally:
                        self.pending_voice_write = False
        except asyncio.CancelledError:
            pass
        except Exception as error:
            detail = ('audio device did not consume PCM for 5 seconds'
                      if isinstance(error, asyncio.TimeoutError)
                      else f'{type(error).__name__}: {error}')
            self.error = f'Playback failed ({self.device}): {detail}'
            print(f'[speaker] {self.error}', file=sys.stderr)
        finally:
            self.timer.cancel()
            await asyncio.gather(self.timer, return_exceptions=True)
            self._set_speaking(False)
            async with self.process_lock:
                await self._stop_player()

    async def close(self):
        # Stop the writer first: cancelling a task racing a completed drain()
        # alone can leave pacat alive and the close waiting for the next frame.
        self.closing = True
        self.generation += 1
        self.interrupted = True
        await self._interrupt_player()
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
