"""Real subprocess reuse, model changes, crash recovery and cancellation."""
import asyncio
import json
import os
import signal
import sys

import pytest

from kufibot_remote.speech_process import SpeechProcess


@pytest.fixture
def worker(tmp_path, monkeypatch):
    script = tmp_path / 'speech_double.py'
    script.write_text('''import json, os, subprocess, sys, time
config = json.loads(sys.stdin.readline())
if config.get('block_loading'):
    time.sleep(120)
print(json.dumps({'type': 'ready'}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request['text'] == 'fail':
        print(json.dumps({'type': 'error', 'message': 'test failure'}), flush=True)
        sys.exit(1)
    if request['text'] == 'block':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        print(json.dumps({'type': 'speaking', 'value': True, 'child_pid': child.pid}), flush=True)
        time.sleep(120)
    print(json.dumps({'type': 'speaking', 'value': True, 'text': request['text']}), flush=True)
    print(json.dumps({'type': 'speaking', 'value': False}), flush=True)
    print(json.dumps({'type': 'done'}), flush=True)
''')
    create = asyncio.create_subprocess_exec
    processes = []

    async def launch(*args, **kwargs):
        process = await create(sys.executable, str(script), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'model')
    (tmp_path / 'voice.onnx.json').write_text('{}')
    return dict(tts=str(model), tts_threads=1, speaker='null'), processes


def test_warm_worker_reused_for_different_texts_and_speakers(worker):
    async def run():
        config, processes = worker
        speech, events = SpeechProcess(), []
        try:
            await speech.prepare(config)
            for text in ['Merhaba!', 'Farklı bir cümle.']:
                await speech.speak({**config, 'speaker': 'other'}, text, events.append)
            assert len(processes) == 1
            assert [e['text'] for e in events if e.get('value')] == ['Merhaba!', 'Farklı bir cümle.']
            assert speech.process.returncode is None
        finally:
            await speech.close()
        assert processes[0].returncode is not None
    asyncio.run(run())


def test_changed_model_file_reloads_worker_and_crash_allows_retry(worker):
    async def run():
        config, processes = worker
        speech = SpeechProcess()
        try:
            await speech.prepare(config)
            with open(config['tts'], 'ab') as model:
                model.write(b'updated')
            await speech.prepare(config)
            assert len(processes) == 2 and processes[0].returncode is not None
            with pytest.raises(RuntimeError, match='test failure'):
                await speech.speak(config, 'fail', lambda _: None)
            assert speech.process is None
            await speech.speak(config, 'Tekrar dene.', lambda _: None)
            assert len(processes) == 3
        finally:
            await speech.close()
    asyncio.run(run())


def test_cancel_stops_worker_and_audio_child_process_group(worker, monkeypatch):
    async def run():
        config, processes = worker
        speech, started = SpeechProcess(), asyncio.Event()
        events, killed = [], []
        killpg = os.killpg
        def terminate(pid, sig):
            killed.append((pid, sig))
            killpg(pid, sig)
        monkeypatch.setattr(os, 'killpg', terminate)
        def on_event(event):
            events.append(event)
            started.set()
        task = asyncio.create_task(speech.speak(config, 'block', on_event))
        try:
            await asyncio.wait_for(started.wait(), 3)
            child_group = os.getpgid(events[0]['child_pid'])
            assert child_group == processes[0].pid
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (child_group, signal.SIGTERM) in killed
            assert processes[0].returncode is not None and speech.process is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await speech.close()
    asyncio.run(run())


def test_cancel_loading_releases_lock_and_allows_another_voice(worker):
    async def run():
        config, processes = worker
        speech = SpeechProcess()
        task = asyncio.create_task(speech.prepare({**config, 'block_loading': True}))
        try:
            async with asyncio.timeout(3):
                while not processes:
                    await asyncio.sleep(.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert processes[0].returncode is not None
            await asyncio.wait_for(speech.prepare(config), 3)
            assert len(processes) == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await speech.close()
    asyncio.run(run())
