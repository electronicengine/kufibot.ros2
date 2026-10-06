"""Async voice lifecycle; all methods run on the voice node's asyncio loop."""
import asyncio
import json
import os
import signal
import sys
import time
import uuid

from .voice_activation import resolve_activation
from .local_voice_runtime import DEFAULTS


class ActivationController:
    def __init__(self, node):
        self.node = node
        self.transition_lock = asyncio.Lock()
        self.mode = 'unavailable'
        self.task = None
        self.worker = None
        self.generation = 0
        self.closed = False
        self.phase = 'idle'
        self.pending_mode = None
        self.paths = {}
        self.config = None
        self.session_generation = None
        self.last_heard = None
        self.last_error = ''
        self.provider_error = False
        self.end_reason = ''
        self.inhibited = False
        self.prevent_start = False
        self.control_mode = None
        self.control_at = 0
        self.listen_not_before = 0

    async def control_state(self, value):
        if value.get('at', 0) < self.control_at:
            return
        self.control_at = value.get('at', 0)
        self.control_mode = value.get('mode')
        self.control_epoch = value['epoch']
        inhibited = value.get('inhibited', False)
        if inhibited != self.inhibited:
            self.inhibited = inhibited
            if self.mode == 'remote':
                await self.settings_changed()

    def state(self, state, detail=''):
        self.phase = state
        self.node._publish_state(state, detail)

    async def terminate_worker(self):
        process, self.worker = self.worker, None
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    async def run_worker(self, operation, **extra):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'kufibot_interaction.activation_worker',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            start_new_session=True, limit=1024 * 1024,
            env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
        self.worker = process
        process.stdin.write((json.dumps({**self.config, 'operation': operation, **extra}) + '\n').encode())
        await process.stdin.drain()
        process.stdin.close()
        result = None
        error = None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                event = json.loads(line)
                if event['type'] == 'ready' and operation == 'listen':
                    self.state('wake_listening', 'Uyanma kelimesi bekleniyor')
                elif event['type'] == 'heard' and operation == 'listen':
                    self.record_heard(event)
                elif event['type'] == 'prepared':
                    result = event['paths']
                elif event['type'] == 'wake':
                    result = True
                elif event['type'] == 'diagnostic':
                    self.node.get_logger().info(event['message'])
                elif event['type'] == 'error':
                    error = event['message']
            await process.wait()
            if error or process.returncode:
                raise RuntimeError(error or f'Ses işlemi başarısız: {process.returncode}')
            return result
        finally:
            await self.terminate_worker()

    def record_heard(self, event):
        text = str(event.get('text', '')).strip()[:2000]
        if text:
            self.last_heard = {'text': text, 'matched': bool(event.get('matched')),
                               'timestamp_ms': int(time.time() * 1000)}
            self.node._activation_updates_pending = True

    async def prepare(self, listen=True):
        from .ai_settings import catalog
        from .audio_devices import local_audio_devices
        config = resolve_activation(self.node.ai_settings['activation'], await asyncio.to_thread(catalog), listen=listen)
        config = {**{key: self.node.get_parameter('local_' + key).value for key in DEFAULTS}, **config}
        mic, speaker = await local_audio_devices('system', self.node.mic_device, self.node.speaker_device)
        config.update(mic=mic, speaker=speaker)
        self.config = config
        self.state('preparing', 'Sabit söyleyişler hazırlanıyor')
        self.paths = await asyncio.wait_for(self.run_worker('prepare'), 120)
        if not self.provider_error:
            self.last_error = ''

    async def request_mode(self, mode):
        request_id = uuid.uuid4().hex
        self.pending_mode = (request_id, mode, asyncio.get_running_loop().create_future())
        try:
            self.node._request_activation_mode(request_id, mode)
            await asyncio.wait_for(self.pending_mode[2], 5)
        finally:
            self.pending_mode = None

    def acknowledge(self, value):
        pending = self.pending_mode
        if pending and value.get('id') == pending[0]:
            if not pending[2].done():
                if (value.get('accepted') and value.get('mode') == pending[1]
                        and (value.get('at', 0) >= self.control_at
                             or value.get('epoch') == getattr(self, 'control_epoch', None))):
                    self.mode = value['mode']
                    self.control_mode = value['mode']
                    self.control_at = value.get('at', 0)
                    self.control_epoch = value.get('epoch', getattr(self, 'control_epoch', None))
                    pending[2].set_result(None)
                else:
                    pending[2].set_exception(RuntimeError('Kontrol modu geçişi iptal edildi'))

    async def cancel(self):
        self.generation += 1
        task, self.task = self.task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.terminate_worker()

    async def mode_changed(self, mode):
        async with self.transition_lock:
            return await self._mode_changed(mode)

    async def _mode_changed(self, mode):
        if self.closed or mode == self.mode:
            return
        if self.control_mode and mode not in (self.control_mode, 'unavailable'):
            return
        self.mode = mode
        if self.pending_mode and mode == self.pending_mode[1]:
            return
        was_audio = self.node.session_active or self.phase in ('greeting', 'farewell', 'session')
        await self.cancel()
        self.node.starting = False
        self.session_generation = None
        await self.node._stop_session()
        if was_audio:
            self.listen_not_before = time.monotonic() + self.node.mic_playback_echo_tail
        self.node._apply_pending_voice_settings()
        if mode == 'ai':
            self.launch(self.return_remote() if self.prevent_start else self.begin())
        elif mode == 'remote':
            self.prevent_start = False
            self.launch(self.listen())
        else:
            self.state('suspended', 'Araç işlemi veya kontrol bağlantısı bekleniyor')

    def launch(self, coroutine):
        self.task = asyncio.create_task(coroutine)

    async def listen(self):
        await asyncio.sleep(max(0, self.listen_not_before - time.monotonic()))
        while not self.closed and self.mode == 'remote':
            if self.inhibited:
                self.state('suspended', 'Workflow testi sırasında dinleme askıda')
                return
            if not self.node.ai_settings['activation']['enabled']:
                self.state('disabled', 'Uyanma dinlemesi kapalı')
                return
            try:
                await self.prepare()
                woke = await self.run_worker('listen')
                if woke:
                    await self.begin(prepared=True)
                    return
                raise RuntimeError('Uyanma dinleyicisi durdu')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.state('error', self.last_error)
                await asyncio.sleep(5)

    async def cue(self, name):
        from .mimics import default_store, evaluate
        self.state('greeting' if name == 'greeting' else 'farewell',
                   'Karşılıyor' if name == 'greeting' else 'Kapanış')
        self.node._reset_expression_turn(enabled=False)
        async def motion():
            mimic = self.config[name + '_mimic']
            if not mimic:
                return
            record = default_store().get(mimic)
            started = time.monotonic()
            while True:
                elapsed = min(record['duration_ms'], int((time.monotonic() - started) * 1000))
                pose = evaluate(record, elapsed)
                self.node._publish_gesture(list(pose), list(pose.values()), .15)
                if elapsed >= record['duration_ms']:
                    break
                await asyncio.sleep(.05)
        async def audio():
            path = self.paths.get(name)
            if path:
                await asyncio.wait_for(self.run_worker('play', path=path), 125)
        tasks = [asyncio.create_task(motion()), asyncio.create_task(audio())]
        try:
            await asyncio.gather(*tasks)
            # Drain playback/room echo before handing capture to another owner.
            if self.paths.get(name):
                await asyncio.sleep(self.node.mic_playback_echo_tail)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def begin(self, prepared=False):
        if self.node.session_active or self.node.starting:
            return
        self.node.starting = True
        self.session_generation = self.generation
        self.end_reason = ''
        try:
            if self.mode != 'ai':
                await self.request_mode('ai')
            try:
                if not prepared:
                    # Do not reuse cue files from an earlier configuration if
                    # preparation fails before assigning the new config.
                    self.config = None
                    self.paths = {}
                    await self.prepare(listen=False)
                await self.cue('greeting')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Local activation cues are optional. In particular, a manual
                # Verasist start must not depend on installed local TTS models.
                await self.terminate_worker()
                self.config = None
                self.paths = {}
                self.node.get_logger().warning(
                    f'Karşılama atlandı; seçilen ses ajanı başlatılıyor: {exc}')
            await self.node._start_session()
            self.phase = 'session'
            self.last_error = ''
            self.provider_error = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.provider_error = True
            await self.node._stop_session()
            self.listen_not_before = time.monotonic() + self.node.mic_playback_echo_tail
            self.state('error', self.last_error)
            await self.return_remote()
        finally:
            self.node.starting = False

    async def finish(self, reason='manual', normal=True):
        async with self.transition_lock:
            return await self._finish(reason, normal)

    async def _finish(self, reason='manual', normal=True):
        self.prevent_start = True
        # Called from a separate task so cleanup can cancel provider reader tasks.
        await self.cancel()
        self.session_generation = None
        self.end_reason = reason
        had_session = self.node.session_active
        await self.node._stop_session()
        self.listen_not_before = time.monotonic() + self.node.mic_playback_echo_tail
        if not normal:
            self.last_error, self.provider_error = reason, True
            self.state('error', reason)
        self.node.starting = False
        async def closing():
            try:
                if normal and had_session and self.config:
                    await self.cue('farewell')
            except Exception as exc:
                self.last_error = str(exc)
                self.state('error', self.last_error)
            finally:
                # Cancellation by a newer mode must never force the robot back to remote mode.
                if not asyncio.current_task().cancelling():
                    await self.return_remote()
        if not self.closed:
            self.launch(closing())

    def ended(self, reason, normal, generation):
        if generation is None or generation != self.session_generation or self.phase in ('farewell', 'idle'):
            return
        self.session_generation = None
        asyncio.create_task(self.finish(reason, normal))

    async def return_remote(self):
        self.node._apply_pending_voice_settings()
        try:
            if self.mode == 'ai':
                await self.request_mode('remote')
        except Exception as exc:
            self.state('error', str(exc))
            return
        if self.mode == 'remote' and not self.closed:
            self.prevent_start = False
            self.launch(self.listen())

    async def settings_changed(self):
        async with self.transition_lock:
            return await self._settings_changed()

    async def _settings_changed(self):
        if self.node.session_active or self.node.starting or self.phase == 'farewell':
            return
        await self.cancel()
        if self.mode == 'remote':
            self.launch(self.listen())

    async def start_requested(self):
        async with self.transition_lock:
            if self.node.session_active or self.node.starting or self.mode == 'tools':
                return
            await self.cancel()
            self.launch(self.begin())

    async def shutdown(self):
        self.closed = True
        async with self.transition_lock:
            await self.cancel()
            await self.node._stop_session()
