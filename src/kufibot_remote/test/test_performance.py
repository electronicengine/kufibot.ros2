import asyncio
from unittest.mock import AsyncMock, Mock

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
        assert performance.control.performance_video_paused
        await asyncio.sleep(0)
        clock[0] += .5
        assert performance.control.tick(.05, NEUTRAL_ANGLES) == (0., 0.)
        assert performance.control.targets['rightArm'] == 40
        clock[0] += .5
        performance.control.tick(.05, NEUTRAL_ANGLES)
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'completed'
        assert performance.control.targets['rightArm'] == 65
        assert not performance.control.performance_video_paused
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
        assert not performance.control.performance_video_paused
    asyncio.run(run())


def test_stop_before_start_and_disconnect_cancel_pending_motion(tmp_path):
    async def run():
        performance, _, command = player(tmp_path)
        performance.start('owner', command)
        await performance.stop()
        assert performance.control.active_mimic is None
        assert performance.status['state'] == 'stopped'
        assert not performance.control.performance_video_paused
        performance.start('owner', command)
        await asyncio.sleep(0)
        assert performance.control.active_mimic is not None
        performance.control.release('owner')
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'stopped'
        assert performance.control.active_mimic is None
        assert not performance.control.performance_video_paused
    asyncio.run(run())


def test_speech_uses_exact_text_and_starts_mimic_on_first_audio(tmp_path):
    async def run():
        performance, clock, command = player(tmp_path)
        performance.speech_config = AsyncMock(return_value=dict(tts='/test/voice.onnx', speaker='null', tts_threads=1))
        started, done = asyncio.Event(), asyncio.Event()
        async def speak(config, text, on_event):
            started.set()
            await done.wait()
        performance.speech.speak = AsyncMock(side_effect=speak)
        performance.start('owner', {**command, 'text': 'Tam olarak bunu söyle.', 'voice': 'fettah'})
        await asyncio.wait_for(started.wait(), 1)
        assert performance.control.active_mimic is None
        assert performance.control.performance_video_paused  # TTS is still preparing.
        args = performance.speech.speak.call_args.args
        assert args[1] == 'Tam olarak bunu söyle.'
        on_event = args[2]
        assert not performance.control.performance_speaking
        on_event({'type': 'speaking', 'value': True})
        assert performance.control.active_mimic['id'] == 'happy'
        assert performance.control.performance_speaking
        on_event({'type': 'speaking', 'value': False})
        assert not performance.control.performance_speaking
        assert performance.control.active_mimic is not None
        assert performance.control.performance_video_paused  # Motion outlasts speech.
        done.set()
        clock[0] += 1
        performance.control.tick(.05, NEUTRAL_ANGLES)
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'completed'
        assert not performance.control.performance_speaking
        assert not performance.control.performance_video_paused
    asyncio.run(run())


def test_video_peers_close_before_mimic_starts_and_offers_wait_until_complete(tmp_path):
    async def run():
        performance, clock, command = player(tmp_path)
        server = Server(performance.control, lambda: {}, Mock())
        performance = server.performance
        closing, closed = asyncio.Event(), asyncio.Event()

        async def close():
            assert performance.control.active_mimic is None
            closing.set()
            await closed.wait()

        peer = Mock(close=AsyncMock(side_effect=close))
        # Start without the background monitor to verify the playback gate itself.
        server.peers.add(peer)
        performance.start('owner', command)
        await asyncio.wait_for(closing.wait(), 1)
        assert server.video_paused()
        assert performance.control.active_mimic is None
        closed.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if performance.control.active_mimic is not None:
                break
        assert performance.control.active_mimic is not None
        assert not server.peers
        peer.close.assert_awaited_once()
        async with TestClient(TestServer(server.app)) as client:
            assert (await client.post('/offer', json={})).status == 503
            server.video_track.assert_not_called()
            clock[0] += 1
            performance.control.tick(.05, NEUTRAL_ANGLES)
            await asyncio.wait_for(performance.task, 1)
            assert not server.video_paused()
            assert (await client.post('/offer', json={})).status == 400
        await server.close()
    asyncio.run(run())


def test_playback_error_resumes_video_without_releasing_voice_pause(tmp_path):
    async def run():
        performance, _, _ = player(tmp_path)
        state = {'voiceStatus': {'active': True}}
        server = Server(performance.control, lambda: state)
        performance = server.performance
        performance.speech_config = AsyncMock(side_effect=ValueError('Ses açılamadı'))
        performance.start('owner', {'text': 'Merhaba'})
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['state'] == 'error'
        assert not performance.control.performance_video_paused
        assert server.video_paused()
        state['voiceStatus'] = {'active': False, 'state': 'idle'}
        assert not server.video_paused()
        await server.close()
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


def test_stop_cancels_speech_and_closes_worker(tmp_path):
    async def run():
        performance, _, command = player(tmp_path)
        performance.speech_config = AsyncMock(return_value={})
        started, stopped = asyncio.Event(), asyncio.Event()
        async def speak(config, text, on_event):
            on_event({'type': 'speaking', 'value': True})
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        performance.speech.speak = AsyncMock(side_effect=speak)
        performance.speech.close = AsyncMock()
        performance.start('owner', {**command, 'text': 'Durdurulabilen konuşma.'})
        await asyncio.wait_for(started.wait(), 1)
        assert performance.control.performance_speaking
        await performance.stop()
        assert stopped.is_set() and not performance.busy
        performance.speech.close.assert_awaited_once()
        assert performance.status['state'] == 'stopped'
        assert performance.control.active_mimic is None
        assert not performance.control.performance_speaking
    asyncio.run(run())


def test_remote_speech_and_mimic_keep_camera_available(tmp_path):
    import numpy as np
    from sensor_msgs.msg import Image
    from kufibot_remote.node import RemoteController

    performance, _, _ = player(tmp_path)
    node = RemoteController.__new__(RemoteController)
    node.control = performance.control
    node.video_max_width = 0
    node.video_frame_time = 0.0
    node.control.performance_speaking = True
    node.control.active_mimic = {'name': 'happy'}
    frame = np.ones((4, 4, 3), np.uint8)
    node._video_image(Image(height=4, width=4, step=12,
                            encoding='bgr8', data=frame.tobytes()))
    assert np.array_equal(node.video_frame, frame)


def test_mimic_only_does_not_wait_for_voice_warmup(tmp_path):
    async def run():
        performance, clock, command = player(tmp_path)
        loading = asyncio.Event()
        performance.speech_config = AsyncMock(return_value={})
        async def prepare(_):
            await loading.wait()
        performance.speech.prepare = AsyncMock(side_effect=prepare)
        performance.prepare('owner')
        await asyncio.sleep(0)
        performance.start('owner', command)
        await asyncio.sleep(0)
        assert performance.control.active_mimic is not None
        clock[0] += 1
        performance.control.tick(.05, NEUTRAL_ANGLES)
        await asyncio.wait_for(performance.task, 1)
        assert performance.status['startup_ms'] < 100
        await performance.stop()
    asyncio.run(run())


def test_playback_does_not_require_microphone_or_accept_disconnected_bluetooth(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_DEVICE_SETTINGS', str(tmp_path / 'devices.json'))
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
