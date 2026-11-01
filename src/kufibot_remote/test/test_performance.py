import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from kufibot_interaction.joint_limits import NEUTRAL_ANGLES
from kufibot_interaction.mimics import MimicStore
from kufibot_remote.control import Control
from kufibot_remote.performance import PerformancePlayer
from kufibot_remote.server import Server


def player(tmp_path):
    clock = [10.]
    control = Control(clock=lambda: clock[0])
    control.mimic_store = MimicStore(tmp_path / 'mimics.json')
    motion = control.mimic_store.save(dict(id='happy', name='Mutlu', description='Gülümse',
        revision=0, duration_ms=1000, interpolation='linear', keyframes=[
            dict(time_ms=0, joints=dict(NEUTRAL_ANGLES)),
            dict(time_ms=1000, joints={**NEUTRAL_ANGLES, 'rightArm': 65})]))
    control.command('owner', {'type': 'claim'})
    control.command('owner', {'type': 'mode', 'mode': 'tools'})
    return PerformancePlayer(control), clock, dict(mimic_id=motion['id'], revision=motion['revision'])


def test_tools_expression_updates_servos_without_driving(tmp_path):
    async def run():
        performance, clock, command = player(tmp_path)
        performance.start('owner', command)
        await asyncio.sleep(0)
        clock[0] += .5
        assert performance.control.tick(.05, NEUTRAL_ANGLES) == (0., 0.)
        assert performance.control.targets['rightArm'] == 40
        clock[0] += .5
        performance.control.tick(.05, NEUTRAL_ANGLES)
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'completed'
        assert performance.control.targets['rightArm'] == 65
    asyncio.run(run())


def test_expression_requires_owner_tools_mode_and_current_revision(tmp_path):
    async def run():
        performance, _, command = player(tmp_path)
        for owner, data in [('other', command), ('owner', {**command, 'revision': 0}),
                            ('owner', {'text': ' ' }), ('owner', {'text': 'a' * 1001})]:
            with pytest.raises(ValueError):
                performance.start(owner, data)
        performance.control.mode = 'ai'
        with pytest.raises(ValueError):
            performance.start('owner', command)
        assert performance.task is None
    asyncio.run(run())


def test_stop_before_start_and_disconnect_cancel_pending_motion(tmp_path):
    async def run():
        performance, _, command = player(tmp_path)
        performance.start('owner', command)
        await performance.stop()
        assert performance.control.active_mimic is None
        assert performance.status['state'] == 'stopped'
        performance.start('owner', command)
        await asyncio.sleep(0)
        assert performance.control.active_mimic is not None
        performance.control.release('owner')
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'stopped'
        assert performance.control.active_mimic is None
    asyncio.run(run())


def test_speech_uses_exact_text_and_starts_mimic_on_first_audio(tmp_path, monkeypatch):
    async def run():
        performance, clock, command = player(tmp_path)
        performance.speech_config = AsyncMock(return_value=dict(tts='/test/voice.onnx', speaker='null', tts_threads=1))
        output = asyncio.StreamReader()
        writes = []
        process = SimpleNamespace(pid=-1, returncode=None, stdout=output,
            stdin=SimpleNamespace(write=writes.append, drain=AsyncMock(), close=lambda: None))
        async def wait():
            process.returncode = 0
            return 0
        process.wait = wait
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        performance.start('owner', {**command, 'text': 'Tam olarak bunu söyle.', 'voice': 'fettah'})
        await asyncio.sleep(.01)
        assert performance.control.active_mimic is None
        assert json.loads(writes[0])['text'] == 'Tam olarak bunu söyle.'
        output.feed_data(b'{"type":"speaking","value":true}\n')
        await asyncio.sleep(.01)
        assert performance.control.active_mimic['id'] == 'happy'
        output.feed_eof()
        clock[0] += 1
        performance.control.tick(.05, NEUTRAL_ANGLES)
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'completed'
    asyncio.run(run())


def test_websocket_performance_and_stop_keep_tools_mode(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    async def run():
        performance, _, command = player(tmp_path)
        performance.control.release('owner')
        server = Server(performance.control, lambda: {})
        server.performance = performance
        async with TestClient(TestServer(server.app)) as client:
            ws = await client.ws_connect('/control')
            async def response(kind):
                while True:
                    data = await ws.receive_json()
                    if data['type'] == kind:
                        return data
            await ws.send_json({'type': 'perform', **command})
            assert 'sahipliği' in (await response('error'))['message']
            await ws.send_json({'type': 'claim'})
            await response('ack')
            await ws.send_json({'type': 'perform', **command})
            await response('ack')
            await asyncio.sleep(.01)
            assert performance.busy
            await ws.send_json({'type': 'stopPerformance'})
            await response('ack')
            assert performance.status['state'] == 'stopped'
            assert performance.control.mode == 'tools'
            assert performance.control.active_mimic is None
            await ws.close()
    asyncio.run(run())


def test_stop_terminates_speech_and_its_audio_process_group(tmp_path, monkeypatch):
    async def run():
        performance, _, command = player(tmp_path)
        performance.speech_config = AsyncMock(return_value={})
        stopped = asyncio.Event()
        process = SimpleNamespace(pid=12345, returncode=None, stdout=asyncio.StreamReader(),
            stdin=SimpleNamespace(write=lambda data: None, drain=AsyncMock(), close=lambda: None))
        async def wait():
            await stopped.wait()
            return process.returncode
        def terminate(pid, sig):
            assert pid == 12345
            process.returncode = -int(sig)
            stopped.set()
        process.wait = wait
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        monkeypatch.setattr('kufibot_remote.performance.os.killpg', terminate)
        performance.start('owner', {**command, 'text': 'Durdurulabilen konuşma.'})
        await asyncio.sleep(.01)
        await performance.stop()
        assert stopped.is_set() and not performance.busy
        assert performance.process is None
        assert performance.status['state'] == 'stopped'
        assert performance.control.active_mimic is None
    asyncio.run(run())


def test_playback_does_not_require_microphone_or_accept_disconnected_bluetooth(tmp_path, monkeypatch):
    async def run():
        performance, _, _ = player(tmp_path)
        monkeypatch.setenv('SPEAKER_ALSA_DEVICE', 'pulse:kufibot_aec_sink')
        monkeypatch.setattr('kufibot_remote.performance.catalog', lambda: [
            dict(id='voice', kind='tts', path='/test.onnx', available=True, languages=['tr'])])
        monkeypatch.setattr('kufibot_remote.performance.read_settings', lambda: {})
        from kufibot_interaction.audio_devices import SPEAKER_PREFIX
        devices = AsyncMock(return_value=[{'name': 'kufibot_aec_sink'}])
        monkeypatch.setattr('kufibot_interaction.audio_devices.pulse_devices', devices)
        with pytest.raises(ValueError, match='Bluetooth hoparlörü'):
            await performance.speech_config('voice')
        devices.return_value.append({'name': SPEAKER_PREFIX + '.1'})
        config = await performance.speech_config('voice')
        assert config['speaker'] == 'pulse:' + SPEAKER_PREFIX + '.1'
        assert all(call.args == ('sinks',) for call in devices.await_args_list)
    asyncio.run(run())
