"""One reusable, cancellable TTS process for the tools console."""
import asyncio
import json
import os
import signal
import sys

from kufibot_interaction.tts_backends import cache_identity


class SpeechProcess:
    def __init__(self):
        self.process = None
        self.key = None
        self.lock = asyncio.Lock()

    async def _send(self, value):
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        await self.process.stdin.drain()

    async def _receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError('Robotun ses işlemi kapandı; tekrar deneyin')
        event = json.loads(line)
        if event.get('type') == 'error':
            raise RuntimeError(event.get('message', 'Robotun ses çıkışı açılamadı'))
        return event

    async def _ready(self, config):
        identity = await asyncio.to_thread(cache_identity, config['tts'])
        key = (config['tts'], config['tts_threads'], identity)
        if self.process is not None and self.process.returncode is None and key == self.key:
            return
        await self.close()
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'kufibot_remote.speech_worker',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            start_new_session=True, limit=1024 * 1024,
            env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
        await self._send(config)
        event = await self._receive()
        if event.get('type') != 'ready':
            raise RuntimeError('Geçersiz ses hazırlama yanıtı')
        self.key = key

    async def prepare(self, config):
        async with self.lock:
            try:
                await self._ready(config)
            except BaseException:
                await self.close()
                raise

    async def speak(self, config, text, on_event):
        async with self.lock:
            try:
                await self._ready(config)
                await self._send({'text': text, 'speaker': config['speaker']})
                while True:
                    event = await self._receive()
                    if event.get('type') == 'done':
                        return
                    on_event(event)
            except BaseException:
                await self.close()
                raise

    async def close(self):
        process, self.process = self.process, None
        self.key = None
        if process is None:
            return
        if process.returncode is None:
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
        if process.stdin is not None:
            process.stdin.close()
