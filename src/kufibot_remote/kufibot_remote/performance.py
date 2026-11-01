"""Owner-bound, cancellable speech and expressions for the tools console."""
import asyncio
import json
import os
import signal
import sys

from kufibot_interaction.ai_settings import catalog, read_settings


class PerformancePlayer:
    def __init__(self, control):
        self.control = control
        self.task = None
        self.process = None
        self.status = {'state': 'idle'}

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    async def options(self):
        models = await asyncio.to_thread(catalog)
        return {'voices': [{'id': m['id'], 'name': m.get('label', m['id']),
                            'languages': m['languages']} for m in models
                           if m['kind'] == 'tts' and m['available']]}

    def check(self, owner, epoch=None):
        if (self.control.owner is not owner or self.control.mode != 'tools'
                or epoch is not None and epoch != self.control.mode_epoch):
            raise ValueError('Araç çağır modu ve kumanda sahipliği gerekli')

    def start(self, owner, data):
        self.check(owner)
        if self.busy:
            raise ValueError('Robot şu anda oynatıyor; önce durdurun')
        text, mimic_id, voice = (data.get(key, '') for key in ('text', 'mimic_id', 'voice'))
        if not all(isinstance(value, str) for value in (text, mimic_id, voice)):
            raise ValueError('Konuşma, mimik ve ses seçimi metin olmalı')
        text = text.strip()
        if len(text) > 1000:
            raise ValueError('Konuşma en fazla 1000 karakter olabilir')
        if len(voice) > 200 or len(mimic_id) > 64:
            raise ValueError('Geçersiz ses veya mimik seçimi')
        request_id = data.get('request_id', '')
        if not isinstance(request_id, str) or len(request_id) > 100:
            raise ValueError('Geçersiz oynatma isteği')
        if not text and not mimic_id:
            raise ValueError('Bir metin yazın veya mimik seçin')
        motion = self.control.mimic_store.get(mimic_id) if mimic_id else None
        if motion and (type(data.get('revision')) is not int or data['revision'] != motion['revision']):
            raise ValueError('Mimik değişti; kitaplığı yenileyip tekrar seçin')
        self.control.stop()
        self.control.disable_navigation('performance_started')
        self.status = dict(state='preparing', text=text, mimic_id=mimic_id,
                           name=motion['name'] if motion else '', error='', request_id=request_id)
        self.task = asyncio.create_task(self._run(owner, self.control.mode_epoch, text, voice, motion))

    async def speech_config(self, voice):
        models = [m for m in await asyncio.to_thread(catalog) if m['kind'] == 'tts' and m['available']]
        settings = await asyncio.to_thread(read_settings)
        preferred = voice or settings.get('activation', {}).get('tts') or settings.get('tts')
        model = next((m for m in models if m['id'] == preferred), None)
        if voice and model is None:
            raise ValueError('Seçilen ses modeli robotta kurulu değil')
        model = model or next((m for m in models if 'tr' in m['languages']), next(iter(models), None))
        if model is None:
            raise ValueError('Robotta kullanılabilir bir ses modeli yok')
        from kufibot_interaction.audio_devices import playback_device
        try:
            speaker = await playback_device(os.environ.get('SPEAKER_ALSA_DEVICE', 'pulse:default'))
        except RuntimeError as error:
            raise ValueError(str(error)) from error
        return dict(tts=model['path'], tts_threads=1,
                    speaker=speaker, text='')

    async def _run(self, owner, epoch, text, voice, motion):
        current = asyncio.current_task()

        async def watch_owner():
            while True:
                await asyncio.sleep(.1)
                try:
                    self.check(owner, epoch)
                except ValueError:
                    current.cancel()
                    return

        watcher = asyncio.create_task(watch_owner())
        started_motion = False

        def play_motion():
            nonlocal started_motion
            self.check(owner, epoch)
            if motion and not started_motion:
                self.control.play_motion(owner, motion, mode='tools')
                started_motion = True
            self.status['state'] = 'playing'

        try:
            async with asyncio.timeout(180):
                if text:
                    config = await self.speech_config(voice)
                    self.check(owner, epoch)
                    self.process = await asyncio.create_subprocess_exec(
                        sys.executable, '-m', 'kufibot_remote.speech_worker',
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        start_new_session=True, limit=1024 * 1024,
                        env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
                    self.process.stdin.write((json.dumps({**config, 'text': text}) + '\n').encode())
                    await self.process.stdin.drain()
                    self.process.stdin.close()
                    error = ''
                    while line := await self.process.stdout.readline():
                        event = json.loads(line)
                        if event.get('type') == 'speaking' and event.get('value'):
                            play_motion()
                        elif event.get('type') == 'error':
                            error = event.get('message', 'Robotun ses çıkışı açılamadı')
                    await self.process.wait()
                    if error or self.process.returncode:
                        raise RuntimeError(error or 'Robotun ses çıkışı açılamadı; hoparlörü kontrol edin')
                else:
                    play_motion()
                while started_motion and self.control.active_mimic is not None:
                    await asyncio.sleep(.05)
                self.check(owner, epoch)
                self.status['state'] = ('completed' if not started_motion
                    or self.control.mimic_status['state'] == 'completed' else 'stopped')
        except asyncio.CancelledError:
            self.status['state'] = 'stopped'
        except Exception as error:
            if (self.control.owner is not owner or self.control.mode != 'tools'
                    or self.control.mode_epoch != epoch):
                self.status['state'] = 'stopped'
            else:
                self.status.update(state='error', error=str(error))
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if started_motion and self.status['state'] != 'completed':
                self.control.cancel_mimic()
            process, self.process = self.process, None
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()

    async def stop(self):
        if self.busy:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.status['state'] = 'stopped'
