import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction.voice_activation import DEFAULT_ACTIVATION, matches, resolve_activation, validate_activation
from kufibot_interaction.activation_controller import ActivationController
from kufibot_interaction import ai_settings
from kufibot_remote.control import Control


@pytest.mark.parametrize('text,phrase,expected', [
    ('Merhaba, Kufi!', 'Kufi', True), ('kufilik', 'Kufi', False),
    ('Hey Kufi nasılsın', 'hey kufi', True), ('hey küçük kufi', 'hey kufi', False),
    ('IŞIK buraya', 'ışık', True), ('İYİ günler', 'iyi', True),
    ('Kufi', '', False), ('Kufi', 'Kufi Kufi', False),
])
def test_word_boundaries_and_turkish(text, phrase, expected):
    assert matches(text, phrase) is expected


def test_activation_validation_and_resolution():
    with pytest.raises(ValueError):
        validate_activation({'enabled': 'yes'})
    with pytest.raises(ValueError):
        validate_activation({'phrase': '!!!'})
    models = [dict(id=id, kind=kind, backend=backend, languages=['tr'], available=True, path='/'+id)
              for id, kind, backend in [('a-hailo', 'stt', 'hailo_whisper'), ('z-vosk', 'stt', 'vosk'), ('voice', 'tts', '')]]
    value = resolve_activation(DEFAULT_ACTIVATION, models)
    assert value['stt'] == '/z-vosk'
    assert value['tts'] == '/voice'
    with pytest.raises(ValueError, match='STT'):
        resolve_activation({**DEFAULT_ACTIVATION, 'stt': 'missing'}, models)


def test_old_settings_upgrade_and_default_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(tmp_path/'ai.json'))
    first = ai_settings.read_settings()
    first['activation']['phrase'] = 'Başka'
    assert ai_settings.read_settings()['activation']['phrase'] == 'Kufi'
    ai_settings.save_settings(first)
    assert ai_settings.read_settings()['activation']['phrase'] == 'Başka'
    owner = object()
    c = Control()
    c.command(owner, {'type': 'claim'})
    old = {k: v for k, v in first.items() if k != 'activation'}
    c.command(owner, {'type': 'setAiSettings', 'settings': old})
    assert 'activation' not in c.ai_settings_requested


def test_remote_boot_and_atomic_remote_handoff():
    c = Control()
    assert c.mode == 'remote'
    assert c.tick(.05, {}) == (0, 0)
    owner = object()
    c.command(owner, {'type': 'claim'})
    c.command(owner, {'type': 'mode', 'mode': 'remote'})
    c.command(owner, {'type': 'input', 'drive_y': -1})
    c.active_mimic = {'id': 'test'}
    request = dict(id='1', mode='ai', expected_mode='remote', epoch=c.mode_epoch)
    assert c.voice_mode(request)
    assert c.mode == 'ai' and c.active_mimic is None
    assert c.tick(.05, {}) == (0, 0)
    assert not any(c.axes.values())
    assert not c.voice_mode(request)
    stale = dict(id='2', mode='remote', expected_mode='ai', epoch=c.mode_epoch)
    c.command(owner, {'type': 'mode', 'mode': 'remote'})
    assert not c.voice_mode(stale)
    c.voice_test_active = True
    assert not c.voice_mode(dict(mode='ai', expected_mode='remote', epoch=c.mode_epoch))


def fake_controller():
    node = SimpleNamespace(session_active=False, starting=False,
        ai_settings={'activation': deepcopy(DEFAULT_ACTIVATION)}, mic_playback_echo_tail=0,
        _publish_state=Mock(), _stop_session=AsyncMock(), _start_session=AsyncMock(),
        get_logger=Mock(return_value=Mock()),
        _apply_pending_voice_settings=Mock(), _reset_expression_turn=Mock(), _publish_gesture=Mock())
    controller = ActivationController(node)
    controller.mode = 'remote'
    return controller, node


def test_greeting_before_session_and_duplicate_start_ignored():
    async def scenario():
        c, node = fake_controller()
        events = []
        async def mode(_):
            events.append('mode')
            c.mode = 'ai'
        async def cue(name):
            events.append(name)
            await c.begin()  # A duplicate wake during greeting is ignored.
        async def start():
            events.append('start')
            node.session_active = True
        c.request_mode = mode
        c.prepare = AsyncMock()
        c.cue = cue
        node._start_session = start
        await c.begin()
        assert events == ['mode', 'greeting', 'start']
        assert not node.starting
    asyncio.run(scenario())


def test_manual_mode_interrupts_greeting_without_late_session():
    async def scenario():
        c, node = fake_controller()
        c.mode = 'ai'
        c.prepare = AsyncMock()
        entered = asyncio.Event()
        async def cue(_):
            entered.set()
            await asyncio.Event().wait()
        c.cue = cue
        c.listen = AsyncMock()
        c.launch(c.begin())
        await entered.wait()
        await c.mode_changed('remote')
        await c.task
        node._start_session.assert_not_awaited()
        assert not node.starting
        c.listen.assert_awaited_once()
    asyncio.run(scenario())


def test_manual_verasist_start_without_local_activation_models(monkeypatch):
    monkeypatch.setattr(ai_settings, 'catalog', lambda: [])

    async def scenario():
        c, node = fake_controller()
        node.ai_settings['provider'] = 'verasist'
        node.ai_settings['activation']['language'] = 'eng'
        c.config, c.paths = {'old': True}, {'farewell': '/old.wav'}
        c.return_remote = AsyncMock()
        await c.mode_changed('ai')
        await c.task
        node._start_session.assert_awaited_once()
        c.return_remote.assert_not_awaited()
        assert c.phase == 'session' and c.mode == 'ai'
        assert c.config is None and c.paths == {}
        assert not node.starting and not c.provider_error
        assert 'TTS' in node.get_logger().warning.call_args.args[0]
    asyncio.run(scenario())


def test_greeting_playback_failure_does_not_block_provider():
    async def scenario():
        c, node = fake_controller()
        c.mode = 'ai'
        c.prepare = AsyncMock()
        c.cue = AsyncMock(side_effect=RuntimeError('playback failed'))
        c.terminate_worker = AsyncMock()
        await c.begin()
        c.terminate_worker.assert_awaited_once()
        node._start_session.assert_awaited_once()
        assert c.phase == 'session' and not c.provider_error
    asyncio.run(scenario())


def test_provider_failure_still_stops_session_and_returns_remote():
    async def scenario():
        c, node = fake_controller()
        c.mode = 'ai'
        c.prepare = AsyncMock(side_effect=ValueError('missing TTS'))
        node._start_session.side_effect = RuntimeError('Verasist connection failed')
        c.return_remote = AsyncMock()
        await c.begin()
        node._stop_session.assert_awaited_once()
        c.return_remote.assert_awaited_once()
        assert c.provider_error and c.last_error == 'Verasist connection failed'
        assert not node.starting
    asyncio.run(scenario())


@pytest.mark.parametrize('normal', [True, False])
def test_end_cleans_provider_before_farewell_and_resumes(normal):
    async def scenario():
        c, node = fake_controller()
        c.mode, c.config = 'ai', {}
        c.config = dict(DEFAULT_ACTIVATION)
        node.session_active = True
        events = []
        async def stop():
            events.append('stop')
            node.session_active = False
        async def cue(name):
            events.append(name)
        async def idle():
            events.append('idle')
        node._stop_session = stop
        c.cue, c.return_remote = cue, idle
        await c.finish('done', normal)
        await c.task
        assert events == (['stop', 'farewell', 'idle'] if normal else ['stop', 'idle'])
    asyncio.run(scenario())


def test_old_session_event_cannot_close_new_session():
    async def scenario():
        c, node = fake_controller()
        c.phase, c.session_generation = 'session', 2
        c.finish = AsyncMock()
        c.ended('old', True, 1)
        c.ended('invalid', True, None)
        await asyncio.sleep(0)
        c.finish.assert_not_awaited()
        c.ended('done', True, 2)
        c.ended('duplicate', True, 2)
        await asyncio.sleep(0)
        c.finish.assert_awaited_once_with('done', True)
    asyncio.run(scenario())


def test_test_mode_and_disabled_listening_never_open_microphone():
    async def scenario():
        c, node = fake_controller()
        c.prepare, c.run_worker = AsyncMock(), AsyncMock()
        c.inhibited = True
        await c.listen()
        assert c.phase == 'suspended'
        c.inhibited = False
        node.ai_settings['activation']['enabled'] = False
        await c.listen()
        assert c.phase == 'disabled'
        c.prepare.assert_not_awaited()
        c.run_worker.assert_not_awaited()
    asyncio.run(scenario())


def test_mode_ack_requires_matching_request():
    async def scenario():
        c, node = fake_controller()
        node._request_activation_mode = Mock()
        task = asyncio.create_task(c.request_mode('ai'))
        await asyncio.sleep(0)
        c.acknowledge({'id':'old', 'mode':'ai', 'accepted':True})
        assert not task.done()
        c.acknowledge({'id':c.pending_mode[0], 'mode':'ai', 'accepted':True, 'epoch':'new'})
        await task
        assert c.mode == 'ai' and c.control_epoch == 'new'
    asyncio.run(scenario())


def test_stale_applied_heartbeat_does_not_undo_acknowledged_handoff():
    async def scenario():
        c, node = fake_controller()
        c.control_mode, c.mode, c.control_at = 'ai', 'ai', 10
        await c.mode_changed('remote')
        await c.control_state({'epoch':'old', 'mode':'remote', 'at':9})
        assert c.mode == c.control_mode == 'ai'
        node._stop_session.assert_not_awaited()
    asyncio.run(scenario())


def test_cached_cue_wav_supports_multiple_chunks_and_reuses_file(tmp_path, monkeypatch):
    from kufibot_interaction.activation_worker import cached_cues
    import wave
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    model = tmp_path/'voice.onnx'
    model.touch()
    (tmp_path/'voice.onnx.json').write_text('{}')
    voice = Mock()
    voice.synthesize.return_value = [SimpleNamespace(sample_channels=1, sample_rate=16000,
                                                    audio_int16_bytes=b'\x00\x00'*100)] * 3
    loader = Mock(return_value=voice)
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.load_voice', loader)
    config = {**DEFAULT_ACTIVATION, 'tts':str(model), 'farewell_text':''}
    paths = cached_cues(config)
    with wave.open(paths['greeting'], 'rb') as stream:
        assert stream.getnframes() == 300
    assert cached_cues(config) == paths
    loader.assert_called_once()
    changed = cached_cues({**config, 'greeting_text':'Yeni metin'})
    assert changed['greeting'] != paths['greeting']


@pytest.mark.parametrize('text,expected', [('Merhaba dünya', False), ('Hey Kufi!', True)])
def test_wake_listener_reports_nonmatching_and_matching_text(text, expected):
    from kufibot_interaction.activation_worker import report_heard
    notifications = []
    assert report_heard(text, DEFAULT_ACTIVATION, lambda kind, **data: notifications.append((kind, data))) is expected
    assert notifications == [('heard', {'text':text, 'matched':expected})]
    controller, node = fake_controller()
    controller.record_heard(notifications[0][1])
    assert controller.last_heard['text'] == text
    assert controller.last_heard['matched'] is expected
    assert controller.last_heard['timestamp_ms'] > 0
    assert node._activation_updates_pending is True
    controller.record_heard({'text':'   ', 'matched':False})
    assert controller.last_heard['text'] == text


def test_idle_is_not_a_control_mode():
    control = Control()
    owner = object()
    control.command(owner, {'type':'claim'})
    with pytest.raises(ValueError, match='Invalid mode'):
        control.command(owner, {'type':'mode', 'mode':'idle'})


@pytest.mark.parametrize('language,expected', [('eng','en'),('EN','en'),('en-US','en'),('tur','tr'),('tr_TR','tr')])
def test_activation_language_aliases(language, expected):
    assert validate_activation({'language':language})['language'] == expected
