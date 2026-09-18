import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kufibot_interaction.local_voice_runtime import DEFAULTS, PhaseLease, SpeechGate, validate_runtime
from kufibot_interaction.local_voice_worker import fit_messages, speak
from kufibot_interaction.hailo_whisper import merge_overlap, package_manifest


def test_phase_lease_expires_and_recovers():
    now = [0.0]
    lease = PhaseLease(clock=lambda: now[0])
    for phase in ('loading', 'listening', 'transcribing', 'synthesizing'):
        lease.receive(json.dumps({'phase': phase}))
        assert not lease.busy
    lease.receive(json.dumps({'phase': 'thinking'}))
    assert lease.busy
    now[0] = 3.1
    assert not lease.busy
    lease.receive('{bad')
    assert not lease.busy
    lease.receive('{"phase":"listening"}')
    assert not lease.busy
    lease.receive('{"phase":"speaking"}')
    assert not lease.busy
    lease.receive('{"phase":"idle"}')
    assert not lease.busy


def test_gate_silence_and_short_noise_do_not_trigger_stt():
    gate = SpeechGate(DEFAULTS)
    for index in range(100):
        assert gate.push(bytes(1024), 0.01, index * .032) == ([], False)
    for index in range(2):
        assert gate.push(bytes(1024), .9, index * .032) == ([], False)
    assert gate.push(bytes(1024), .01, 5) == ([], False)


def test_gate_preserves_preroll_and_pause_then_endpoints():
    gate = SpeechGate(DEFAULTS)
    for index in range(10):
        gate.push(bytes([index]) * 1024, .01, index * .032)
    for index in range(3):
        frames, done = gate.push(b'x' * 1024, .9, .32 + index * .032)
    assert len(frames) == 11 and frames[-3:] == [b'x' * 1024] * 3
    assert not done
    for index in range(10):
        _, done = gate.push(bytes(1024), .01, 1)
        assert not done
    gate.push(b'x' * 1024, .9, 2)
    assert gate.last_voice == 2
    for index in range(19):
        _, done = gate.push(bytes(1024), .01, 3 + index * .032)
    assert done and gate.last_voice == 2


def test_maximum_utterance_is_bounded():
    gate = SpeechGate({**DEFAULTS, 'max_utterance_sec': .2})
    done = False
    for index in range(8):
        _, done = gate.push(bytes(1024), .9, index * .032)
        if done:
            break
    assert done


@pytest.mark.parametrize('change', [{'vad_threshold': 2}, {'llm_threads': 0},
                                   {'llm_threads': 2.5}, {'llm_context': 160},
                                   {'hailo_timeout_sec': float('nan')}])
def test_invalid_runtime_values_rejected(change):
    with pytest.raises(ValueError):
        validate_runtime(change)


def test_context_budget_uses_rendered_template_and_drops_old_pairs():
    llm = Mock()
    llm.n_ctx.return_value = 180
    llm.tokenize.side_effect = lambda value, **_: list(value)
    formatter = lambda messages: SimpleNamespace(prompt='TEMPLATE' + ''.join(m['content'] for m in messages))
    system = {'role': 'system', 'content': 'sys'}
    history = [{'role': 'user', 'content': 'a' * 80}, {'role': 'assistant', 'content': 'b' * 80}]
    result = fit_messages(llm, formatter, system, history, 'hi', 30)
    assert result == [system, {'role': 'user', 'content': 'hi'}]
    with pytest.raises(ValueError, match='System prompt'):
        fit_messages(llm, formatter, {'role': 'system', 'content': 'x' * 300}, [], 'hi', 30)


def test_overlap_only_removes_suffix_prefix():
    assert merge_overlap('Merhaba, dünya!', 'Dünya bugün güzel.') == 'Merhaba, dünya! bugün güzel.'
    assert merge_overlap('evet evet', 'hayır evet') == 'evet evet hayır evet'
    assert merge_overlap('', 'hello') == 'hello'


def test_corrupt_or_incomplete_hailo_package_rejected(tmp_path):
    (tmp_path / 'manifest.json').write_text(json.dumps(dict(backend='hailo_whisper', arch='hailo8l', variant='tiny', languages=['tr', 'en'], files={})))
    with pytest.raises(ValueError, match='Incomplete'):
        package_manifest(tmp_path)


def test_tts_writes_first_chunk_before_synthesizing_next(monkeypatch):
    import os
    from kufibot_interaction import local_voice_worker as worker
    read_fd, write_fd = os.pipe()
    player = Mock()
    player.stdin = os.fdopen(write_fd, 'wb', buffering=0)
    player.wait.return_value = 0
    player.poll.return_value = 0
    monkeypatch.setattr(worker.subprocess, 'Popen', Mock(return_value=player))
    events = []
    monkeypatch.setattr(worker, 'emit', lambda kind, **kw: events.append((kind, kw)))
    reporter = Mock()
    chunk = SimpleNamespace(sample_rate=16000, sample_channels=1, audio_int16_bytes=b'abc')
    def chunks(_):
        yield chunk
        assert os.read(read_fd, 3) == b'abc'
        reporter.mark.assert_any_call('playback_start')
        yield chunk
    try:
        speak({'speaker': 'null'}, SimpleNamespace(synthesize=chunks), 'hello', reporter)
        assert os.read(read_fd, 3) == b'abc'
    finally:
        os.close(read_fd)
    assert events == [('speaking', {'value': True}), ('speaking', {'value': False})]


def test_embedding_suspended_start_never_loads_and_uses_fallback():
    from kufibot_interaction.expression_embedding import ExpressionWorker
    library = Mock()
    library.classify.return_value = 'happy'
    factory = Mock()
    worker = ExpressionWorker(library, factory, Mock(), suspended=True)
    try:
        worker.invalidate(1)
        worker.submit(1, 'hello')
        assert worker.poll()[1] == 'happy'
        assert worker.wait_idle(.1)
        factory.assert_not_called()
        worker.suspend(False)
        deadline = time.monotonic() + 1
        while not factory.called and time.monotonic() < deadline:
            time.sleep(.01)
        factory.assert_called_once()
    finally:
        worker.close()


@pytest.mark.parametrize('recognized', ['merhaba', ''])
def test_vad_capture_closes_mic_before_stt_finalize(monkeypatch, recognized):
    import subprocess
    import sys
    from kufibot_interaction import local_voice_worker as worker
    original = subprocess.Popen
    processes = []
    def launch(*_, **kwargs):
        p = original([sys.executable, '-c',
                      'import os,time; os.write(1, bytes(1024*25)); time.sleep(10)'], **kwargs)
        processes.append(p)
        return p
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    monkeypatch.setattr(worker, 'emit', Mock())
    vad = Mock(side_effect=[.9] * 3 + [.01] * 22)
    backend = Mock(timings={})
    def finish(**kwargs):
        assert kwargs["trailing_silence_ms"] == 608
        assert processes[0].poll() is not None
        return recognized
    backend.finish.side_effect = finish
    reporter = Mock()
    result = worker.listen_vad({**DEFAULTS, 'mic': 'test'}, backend, vad, reporter)
    assert result == recognized
    assert backend.feed.call_count == 22
    reporter.set_phase.assert_any_call('transcribing')
    reporter.mark.assert_any_call('stt_final', audio_seconds=22 * .032,
        rms_dbfs=-120.0, peak=0.0, clipped_samples=0, empty=not bool(recognized))
    if not recognized:
        worker.emit.assert_any_call('state', state='listening',
            detail='Söylediğinizi anlayamadım; lütfen tekrar konuşun.')


def test_worker_phases_publish_and_heartbeat_loss_stops(monkeypatch):
    import asyncio
    from kufibot_interaction.voice_agent_node import VoiceAgentNode
    from unittest.mock import AsyncMock
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.local_phase_pub = Mock()
    node.local_metrics_pub = Mock()
    node.get_logger = Mock()
    node._stop_session = AsyncMock()
    node._publish_state = Mock()
    process = SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
        b'{"type":"phase","phase":"thinking"}\n',
        b'{"type":"metric","event":"llm_start","turn":1,"monotonic_sec":1}\n',
        asyncio.TimeoutError()])))
    node.local_process = process
    asyncio.run(node._local_events(process))
    assert json.loads(node.local_phase_pub.publish.call_args.args[0].data)['phase'] == 'thinking'
    node.local_metrics_pub.publish.assert_called_once()
    node._stop_session.assert_awaited_once()
    node._publish_state.assert_called_with('error', 'Local voice heartbeat timed out')


def test_selected_language_prefix_is_not_hardcoded():
    # This runs only when optional tokenizer assets are installed on the target.
    from pathlib import Path
    if not Path('/usr/local/ai.models/whisper-tiny-hailo8l/tokenizer.json').exists():
        pytest.skip('Offline tokenizer not installed')
    tokenizers = pytest.importorskip('tokenizers')
    tokenizer = tokenizers.Tokenizer.from_file('/usr/local/ai.models/whisper-tiny-hailo8l/tokenizer.json')
    from kufibot_interaction.hailo_whisper import decoder_prefix
    for language in ('tr', 'en'):
        prefix = decoder_prefix(tokenizer, language)
        assert tokenizer.id_to_token(prefix[1]) == f'<|{language}|>'
        assert tokenizer.id_to_token(prefix[2]) == '<|transcribe|>'


def test_hailo_windowing_does_not_discard_repeated_sentences():
    import numpy as np
    from kufibot_interaction.hailo_whisper import HailoWhisper
    backend = HailoWhisper.__new__(HailoWhisper)
    backend.samples = 160000
    backend.audio = bytearray(bytes(32000 * 8))
    backend._bounded_window = Mock(side_effect=['one two three four five', 'four five six seven eight', 'eight nine ten'])
    assert backend.finish() == 'one two three four five six seven eight nine ten'
    assert backend._bounded_window.call_count == 3
    assert max(len(call.args[0]) for call in backend._bounded_window.call_args_list) <= 48000
    assert merge_overlap('one two three four five', 'one two three four five') == 'one two three four five one two three four five'


def test_hailo_retry_preserves_shared_deadline():
    import numpy as np
    from kufibot_interaction.hailo_whisper import HailoWhisper, DecoderLimitError
    backend = HailoWhisper.__new__(HailoWhisper)
    backend.timeout = 30
    backend._transcribe_window = Mock(side_effect=[DecoderLimitError(), 'hello world', 'world again'])
    assert backend._bounded_window(np.zeros(48000), deadline=42) == 'hello world again'
    assert all(call.args[1] == 42 for call in backend._transcribe_window.call_args_list)


def test_hailo_timeout_prevents_native_call():
    from kufibot_interaction.hailo_whisper import HailoWhisper
    backend = HailoWhisper.__new__(HailoWhisper)
    native = Mock()
    with pytest.raises(TimeoutError):
        backend._run(native, Mock(), time.monotonic() - 1)
    native.run.assert_not_called()


def test_hailo_endpoint_silence_does_not_create_an_extra_window():
    from kufibot_interaction.hailo_whisper import HailoWhisper
    backend = HailoWhisper.__new__(HailoWhisper)
    backend.samples = 160000
    backend.audio = bytearray(bytes(int(3.104 * 32000)))
    backend._bounded_window = Mock(return_value='hello')
    assert backend.finish(trailing_silence_ms=608) == 'hello'
    backend._bounded_window.assert_called_once()
    assert len(backend._bounded_window.call_args.args[0]) < 48000


def test_heartbeat_survives_gil_holding_native_load():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c', '''
import ctypes
from kufibot_interaction.local_voice_worker import emit
from kufibot_interaction.local_voice_runtime import Reporter
r = Reporter(emit)
r.set_phase('loading')
ctypes.PyDLL(None).usleep(2300000)
r.close()
'''], capture_output=True, text=True, timeout=6, check=True)
    phases = [json.loads(line) for line in result.stdout.splitlines()]
    loading = [e for e in phases if e.get('phase') == 'loading']
    assert len(loading) >= 3
    assert max(b['monotonic_sec'] - a['monotonic_sec'] for a, b in zip(loading, loading[1:])) < 1.5
    assert phases[-1]['phase'] == 'idle'


def test_heartbeat_dies_with_inference_owner():
    import subprocess
    import sys
    process = subprocess.Popen([sys.executable, '-c', '''
import time
from kufibot_interaction.local_voice_worker import emit
from kufibot_interaction.local_voice_runtime import Reporter
r = Reporter(emit)
r.set_phase('loading')
time.sleep(100)
'''], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert json.loads(process.stdout.readline())['phase'] == 'loading'
        process.kill()
        # EOF requires the helper to release its inherited stdout as well.
        process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_cold_python_startup_renews_loading_lease():
    import asyncio
    from unittest.mock import AsyncMock
    from kufibot_interaction.voice_agent_node import VoiceAgentNode
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.local_phase_pub = Mock()
    node._stop_session = AsyncMock()
    node._publish_state = Mock()
    process = SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=[
        asyncio.TimeoutError(), asyncio.TimeoutError(),
        b'{"type":"phase","phase":"loading"}\n', b''])))
    node.local_process = process
    asyncio.run(node._local_events(process))
    assert [json.loads(c.args[0].data)['phase'] for c in node.local_phase_pub.publish.call_args_list] == ['loading'] * 3
    node._stop_session.assert_awaited_once()
