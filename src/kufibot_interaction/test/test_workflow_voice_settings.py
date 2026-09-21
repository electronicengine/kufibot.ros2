import asyncio
import json
import signal
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction.ai_settings import DEFAULT, workflow_voice_settings
from kufibot_interaction.local_voice_settings import apply_voice_options, validate_voice_options
from kufibot_interaction.local_voice_runtime import DEFAULTS, validate_runtime, SessionDeadline, SessionTimeout
from kufibot_interaction.workflows import WorkflowStore, validate_graph


def flow(voice):
    return {'schema_version': 1, 'id': 'voice-options', 'revision': 0, 'name': 'Ses ayarları',
            'settings': {**DEFAULT, 'voice': voice},
            'nodes': [{'id': 'start', 'type': 'start', 'data': {}}, {'id': 'agent', 'type': 'agent', 'data': {}}],
            'edges': [{'source': 'start', 'target': 'agent'}]}


def test_options_persist_without_changing_provider_schema_or_robot_defaults(tmp_path):
    options = {'max_session_sec': 120, 'vad_threshold': .7, 'vad_silence_ms': 900, 'aec_mode': 'enabled'}
    store = WorkflowStore(tmp_path)
    saved = store.save(flow(options))
    store.publish(saved)
    assert store.snapshot(saved['id'])['settings']['voice'] == options
    assert workflow_voice_settings(saved) == DEFAULT
    assert apply_voice_options({'vad_threshold': .4, 'tts_threads': 2}, saved) == {
        **options, 'tts_threads': 2}
    assert apply_voice_options({'vad_threshold': .4}, flow({})) == {'vad_threshold': .4}
    assert validate_runtime({**DEFAULTS, **options})['max_session_sec'] == 120


@pytest.mark.parametrize('milliseconds', [32, 300, 1500, 2000])
def test_preroll_setting_reaches_published_voice_session(tmp_path, milliseconds):
    store = WorkflowStore(tmp_path)
    saved = store.save(flow({'vad_pre_roll_ms': milliseconds}))
    store.publish(saved)
    config = apply_voice_options(DEFAULTS, store.snapshot(saved['id']))
    assert validate_runtime(config)['vad_pre_roll_ms'] == milliseconds
    assert DEFAULTS['vad_pre_roll_ms'] == 300


@pytest.mark.parametrize('options', [
    {'max_session_sec': -1}, {'max_session_sec': 14401}, {'max_session_sec': True},
    {'vad_threshold': float('nan')}, {'vad_threshold': 1}, {'vad_silence_ms': 0},
    {'vad_min_speech_ms': 5001}, {'vad_pre_roll_ms': 2500}, {'max_utterance_sec': 121},
    {'aec_mode': 'unknown'}, {'unknown': 1}, {'vad_silence_ms': 50.5},
])
def test_invalid_voice_options_never_replace_saved_workflow(tmp_path, options):
    store = WorkflowStore(tmp_path)
    saved = store.save(flow({}))
    with pytest.raises(ValueError):
        validate_voice_options(options)
    assert validate_graph({**saved, 'settings': {'voice': options}})
    with pytest.raises(ValueError):
        store.save({**saved, 'settings': {'voice': options}})
    assert store.get(saved['id']) == saved


def test_deadline_interrupts_wait_and_restores_signal_handler():
    before = signal.getsignal(signal.SIGALRM)
    deadline = SessionDeadline(.03)
    try:
        deadline.start()
        with pytest.raises(SessionTimeout):
            time.sleep(1)
    finally:
        deadline.close()
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
    unlimited = SessionDeadline(0)
    unlimited.start()
    unlimited.close()
    assert signal.getsignal(signal.SIGALRM) == before


@pytest.mark.parametrize('during', ['listening', 'generation'])
def test_session_limit_finishes_recording_even_without_a_completed_turn(tmp_path, monkeypatch, capsys, during):
    from kufibot_interaction import local_voice_worker as worker
    stub = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, 'llama_cpp', SimpleNamespace(Llama=lambda **kwargs: SimpleNamespace(close=stub)))
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(disable_telemetry_events=stub))
    monkeypatch.setattr(worker, 'Reporter', lambda notify: SimpleNamespace(turn=0, set_phase=stub, mark=stub, close=stub))
    monkeypatch.setattr(worker, 'create_stt', lambda config: SimpleNamespace(close=stub))
    monkeypatch.setattr(worker, 'SileroVad', stub)
    monkeypatch.setattr(worker, 'make_formatter', stub)
    monkeypatch.setattr(worker, 'load_voice', stub)
    monkeypatch.setattr(worker, 'fit_messages', lambda *args: [])
    def waiting(*args, **kwargs):
        time.sleep(10)
        raise AssertionError('Session did not time out')
    monkeypatch.setattr(worker, 'listen_vad', waiting if during == 'listening' else lambda *args: 'Merhaba')
    monkeypatch.setattr(worker, 'generate_and_speak', waiting)
    worker.run({**DEFAULTS, 'language': 'tr', 'llm': 'unused', 'recording_root': str(tmp_path), 'max_session_sec': 1})
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {'type': 'session_complete', 'reason': 'max_duration'} in events
    info = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert 1 <= info['duration_sec'] < 2
    assert events[-1]['status'] == 'complete'
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_aec_routing_uses_paired_devices_and_fails_when_unavailable(monkeypatch):
    from kufibot_interaction import audio_devices as audio
    async def devices(kind):
        return [{'name': name} for name in ([audio.CAMERA_SOURCE, 'kufibot_aec_source'] if kind == 'sources'
                                            else [audio.SPEAKER_PREFIX + '.1', 'kufibot_aec_sink'])]
    monkeypatch.setattr(audio, 'pulse_devices', devices)
    async def scenario():
        assert await audio.local_audio_devices('enabled', 'plughw:2,0', 'default') == audio.AEC_PAIR
        assert await audio.local_audio_devices('disabled', *audio.AEC_PAIR) == (
            'pulse:' + audio.CAMERA_SOURCE, 'pulse:' + audio.SPEAKER_PREFIX + '.1')
        monkeypatch.setattr(audio, 'pulse_default', AsyncMock(side_effect=['pulse:kufibot_aec_source', 'pulse:custom-speaker']))
        assert await audio.local_audio_devices('disabled', 'pulse', 'pulse:default') == ('pulse:' + audio.CAMERA_SOURCE, 'pulse:custom-speaker')
        assert await audio.local_audio_devices('disabled', 'plughw:2,0', 'default') == ('plughw:2,0', 'default')
        assert await audio.local_audio_devices('system', 'plughw:2,0', 'hw:0') == ('plughw:2,0', 'hw:0')
        assert await audio.local_audio_devices('disabled', 'plughw:2,0', 'hw:0') == ('plughw:2,0', 'hw:0')
        monkeypatch.setattr(audio, 'pulse_devices', AsyncMock(return_value=[]))
        with pytest.raises(RuntimeError, match='unavailable'):
            await audio.local_audio_devices('enabled', 'plughw:2,0', 'default')
    asyncio.run(scenario())
    assert audio.audio_command(True, audio.AEC_PAIR[0], 16000, 1)[0] == 'parec'
    assert '--device=kufibot_aec_source' in audio.audio_command(True, audio.AEC_PAIR[0], 16000, 1)
    assert audio.audio_command(False, audio.AEC_PAIR[1], 16000, 1)[0] == 'pacat'
    assert '--device=kufibot_aec_sink' in audio.audio_command(False, audio.AEC_PAIR[1], 16000, 1)


def test_voice_node_passes_workflow_options_into_normal_local_session(tmp_path, monkeypatch):
    from kufibot_interaction import voice_agent_node as module
    from kufibot_interaction import audio_devices
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path))
    store = WorkflowStore()
    document = flow({'max_session_sec': 90, 'vad_silence_ms': 800, 'aec_mode': 'enabled'})
    document['settings'].update(provider='local', **{kind: kind for kind in ('stt', 'llm', 'embedding', 'tts')})
    store.publish(store.save(document))
    monkeypatch.setattr(module, 'validate', lambda value: value)
    monkeypatch.setattr(module, 'catalog', lambda: [{'id': kind, 'path': '/fake/' + kind} for kind in ('stt', 'llm', 'embedding', 'tts')])
    audio = AsyncMock(return_value=audio_devices.AEC_PAIR)
    monkeypatch.setattr(audio_devices, 'local_audio_devices', audio)
    node = module.VoiceAgentNode.__new__(module.VoiceAgentNode)
    node.ai_settings = {**document['settings'], 'workflow_id': document['id']}
    node._is_local = lambda: True
    node.get_parameter = lambda name: SimpleNamespace(value=DEFAULTS[name.removeprefix('local_')])
    node.expression_worker = Mock(wait_idle=Mock(return_value=True))
    node.get_logger = Mock()
    node.mic_device, node.speaker_device = 'plughw:2,0', 'default'
    node._reset_expression_turn = node._publish_state = Mock()
    node.expressions_available = False
    node.local_phase_pub = Mock()
    node._local_events = AsyncMock()
    process = SimpleNamespace(stdin=Mock(drain=AsyncMock()))
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    async def scenario():
        await node._start_session_unlocked()
        await node.local_task
    asyncio.run(scenario())
    config = json.loads(process.stdin.write.call_args.args[0])
    assert config['max_session_sec'] == 90
    assert config['vad_silence_ms'] == 800
    assert config['vad_threshold'] == DEFAULTS['vad_threshold']
    assert (config['mic'], config['speaker']) == audio_devices.AEC_PAIR
    audio.assert_awaited_once_with('enabled', 'plughw:2,0', 'default')


def test_parent_deadline_never_stops_a_replacement_session(monkeypatch):
    from kufibot_interaction import voice_agent_node as module
    node = module.VoiceAgentNode.__new__(module.VoiceAgentNode)
    old, node.local_process = object(), object()
    node._stop_session, node._publish_state = AsyncMock(), Mock()
    monkeypatch.setattr(module.asyncio, 'sleep', AsyncMock())
    asyncio.run(node._enforce_local_session_limit(old, 10))
    node._stop_session.assert_not_awaited()
    asyncio.run(node._enforce_local_session_limit(node.local_process, 10))
    node._stop_session.assert_awaited_once()
    node._publish_state.assert_called_once_with('idle', 'Azami görüşme süresine ulaşıldı')
