"""Owner-bound, cancellable speech and expressions for the tools console."""
import asyncio
import os
import time

from kufibot_interaction.ai_settings import catalog, read_settings
from .speech_process import SpeechProcess


class PerformancePlayer:
    def __init__(self, control):
        self.control = control
        self.task = None
        self.speech = SpeechProcess()
        self.warm_task = None
        self.warm_voice = None
        self.status = {'state': 'idle'}

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    async def options(self):
        models = await asyncio.to_thread(catalog)
        return {'voices': [{'id': m['id'], 'name': m.get('label', m['id']),
                            'languages': m['languages']} for m in models
                           if m['kind'] == 'tts' and m['available']]}

    def prepare(self, owner, voice=''):
        """Load the selected voice while the operator chooses text and mimic."""
        self.check(owner)
        if not isinstance(voice, str) or len(voice) > 200:
            raise ValueError('Geçersiz ses seçimi')
        if self.busy or (self.warm_task and not self.warm_task.done() and self.warm_voice == voice):
            return
        if self.warm_task:
            self.warm_task.cancel()
        self.warm_voice = voice
        self.warm_task = asyncio.create_task(self._prepare(voice))

    async def _prepare(self, voice):
        try:
            async with asyncio.timeout(120):
                await self.speech.prepare(await self.speech_config(voice))
        except (Exception, asyncio.CancelledError):
            # Playback retries preparation and reports any device/model error.
            pass

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
        self.started_at = time.monotonic()
        self.task = asyncio.create_task(self._run(owner, self.control.mode_epoch, text, voice, motion))

    async def speech_config(self, voice):
        available, settings = await asyncio.gather(
            asyncio.to_thread(catalog), asyncio.to_thread(read_settings))
        models = [m for m in available if m['kind'] == 'tts' and m['available']]
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
        return dict(tts=model['path'], tts_threads=1, speaker=speaker)

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
            self.status.setdefault('startup_ms', round((time.monotonic() - self.started_at) * 1000))

        def speech_event(event):
            if event.get('type') == 'speaking':
                self.control.performance_speaking = bool(event.get('value'))
                if self.control.performance_speaking:
                    play_motion()

        try:
            async with asyncio.timeout(180):
                if text:
                    config = await self.speech_config(voice)
                    self.check(owner, epoch)
                    await self.speech.speak(config, text, speech_event)
                    self.control.performance_speaking = False
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
            self.control.performance_speaking = False

    async def stop(self):
        if self.warm_task:
            self.warm_task.cancel()
            await asyncio.gather(self.warm_task, return_exceptions=True)
            self.warm_task = None
        if self.busy:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.status['state'] = 'stopped'
        await self.speech.close()
