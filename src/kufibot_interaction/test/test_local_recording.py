import json
import wave

from kufibot_interaction.local_recording import SessionRecording


def test_session_recording_merges_user_and_robot_into_stereo_wav(tmp_path):
    recording = SessionRecording(tmp_path)
    recording.write('user', b'\x01\x00' * 160)
    recording.write('assistant', b'\x02\x00' * 160, 16000)
    info = recording.close()
    assert info['channels'] == {'left': 'user', 'right': 'assistant'}
    assert json.loads((tmp_path / f"{info['id']}.json").read_text())['id'] == info['id']
    with wave.open(str(tmp_path / f"{info['id']}.wav"), 'rb') as sound:
        assert (sound.getnchannels(), sound.getframerate()) == (2, 16000)
        assert sound.getnframes() >= 160
        data = sound.readframes(sound.getnframes())
        assert b'\x01\x00' in data and b'\x02\x00' in data


def test_archive_clock_final_messages_snapshot_and_session_isolation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kufibot_interaction import local_recording
    clock = [10.0]
    monkeypatch.setattr(local_recording, 'time', SimpleNamespace(
        time=lambda: 1000, monotonic=lambda: clock[0], strftime=lambda pattern: 'session-'))
    workflow = {'id': 'flow', 'name': 'Karşılama', 'nodes': [{'id': 'start', 'data': {'name': 'Başlangıç'}}]}
    recording = SessionRecording(tmp_path, workflow)
    workflow['nodes'][0]['data']['name'] = 'Değişti'
    clock[0] = 10.1
    recording.record_event('transcript', role='user', text='Mer', final=False)
    clock[0] = 10.2
    recording.record_event('transcript', role='user', text='Merhaba', final=False)
    clock[0] = 10.5
    recording.record_event('transcript', role='user', text='Merhaba')
    recording.record_event('workflow_event', event={'type': 'answer', 'text': 'Duplicate'})
    payload = {'type': 'tool_start', 'name': 'look', 'arguments': {'direction': 'left'}}
    recording.record_event('workflow_event', event=payload)
    payload['arguments']['direction'] = 'right'
    recording.record_event('workflow_event', event={'type': 'tool_result', 'name': 'look', 'result': {'status': 'error'}})
    recording.record_event('workflow_event', event={'type': 'transition', 'from_node': 'start', 'node_id': 'end'})
    clock[0] = 11.0
    info = recording.close()
    assert info['duration_sec'] == 1
    assert info['workflow']['nodes']['start'] == 'Başlangıç'
    assert len(info['events']) == 4
    assert info['events'][0]['text'] == 'Merhaba'
    assert info['events'][0]['offset_ms'] == 100
    assert [event['sequence'] for event in info['events']] == [0, 1, 2, 3]
    assert info['events'][1]['arguments']['direction'] == 'left'
    assert json.loads((tmp_path / f"{recording.id}.json").read_text()) == info
    with wave.open(str(tmp_path / f"{recording.id}.wav")) as sound:
        assert sound.getnframes() == 16000
        assert set(sound.readframes(16000)) == {0}
    assert recording.close() == info
    recording.write('user', b'\x01\x00')
    recording.record_event('transcript', role='user', text='Late')
    assert len(info['events']) == 4
    other = SessionRecording(tmp_path)
    assert other.close()['events'] == []


def test_waveform_extrema_stereo_and_bounded_size(tmp_path):
    from struct import pack
    from kufibot_interaction.local_recording import recording_waveform
    path = tmp_path / 'sound.wav'
    with wave.open(str(path), 'wb') as sound:
        sound.setnchannels(2); sound.setsampwidth(2); sound.setframerate(16000)
        sound.writeframes(pack('<hhhh', -32768, 8192, 16384, -8192) * 2000)
    result = recording_waveform(path, max_bins=100)
    assert result['duration_sec'] == .25
    assert len(result['channels']['user']) == 100
    assert all(value == [-1.0, .5] for value in result['channels']['user'])
    assert all(value == [-.25, .25] for value in result['channels']['assistant'])


def test_worker_emitter_archives_only_current_recording(tmp_path, monkeypatch, capsys):
    from kufibot_interaction import local_voice_worker
    recording = SessionRecording(tmp_path)
    monkeypatch.setattr(local_voice_worker, '_session_recording', recording)
    local_voice_worker.emit('transcript', role='assistant', text='Merhaba', final=False)
    local_voice_worker.emit('transcript', role='assistant', text='Merhaba!')
    local_voice_worker.emit('workflow_event', event={'type': 'node', 'node_id': 'hello'})
    assert [item['type'] for item in recording.close()['events']] == ['transcript', 'node']
    assert len(capsys.readouterr().out.splitlines()) == 3


def test_worker_run_finalizes_archive_when_interrupted(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace
    import pytest
    from kufibot_interaction import local_voice_worker as worker
    stub = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, 'llama_cpp', SimpleNamespace(Llama=lambda **kwargs: SimpleNamespace(close=stub)))
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(disable_telemetry_events=stub))
    monkeypatch.setattr(worker, 'validate_runtime', lambda config: config)
    monkeypatch.setattr(worker, 'Reporter', lambda notify: SimpleNamespace(turn=0, set_phase=stub, mark=stub, close=stub))
    monkeypatch.setattr(worker, 'create_stt', lambda config: SimpleNamespace(close=stub))
    monkeypatch.setattr(worker, 'SileroVad', stub)
    monkeypatch.setattr(worker, 'make_formatter', stub)
    monkeypatch.setattr(worker, 'load_voice', stub)
    monkeypatch.setattr(worker, 'listen_vad', lambda *args: 'Merhaba')
    monkeypatch.setattr(worker, 'fit_messages', lambda *args: [])
    def interrupt(*args, recorder=None, **kwargs):
        recorder.write('user', b'\x01\x00' * 160)
        worker.emit('transcript', role='assistant', text='Hoş geldiniz')
        raise SystemExit(0)
    monkeypatch.setattr(worker, 'generate_and_speak', interrupt)
    config = dict(language='tr', llm='unused', llm_threads=1, llm_batch_threads=1,
                  vad_model='unused', llm_context=1024, llm_max_tokens=32, recording_root=str(tmp_path))
    with pytest.raises(SystemExit):
        worker.run(config, max_turns=1)
    files = list(tmp_path.glob('*.json'))
    assert len(files) == 1
    info = json.loads(files[0].read_text())
    assert [event['text'] for event in info['events']] == ['Merhaba', 'Hoş geldiniz']
    assert (tmp_path / info['file']).is_file()
    assert worker._session_recording is None
    notifications = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert notifications[-1]['status'] == 'complete'
    assert 'events' not in notifications[-1]


def test_sigterm_handler_allows_finalization_in_worker_process(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    from kufibot_interaction import local_voice_worker
    source = str(Path(local_voice_worker.__file__).resolve().parents[1])
    code = '''
import io, os, signal, sys
from kufibot_interaction import local_voice_worker as worker
from kufibot_interaction.local_recording import SessionRecording

def run(config):
    recording = SessionRecording(config['root'])
    try:
        recording.record_event('transcript', role='user', text='Stopped')
        os.kill(os.getpid(), signal.SIGTERM)
    finally:
        recording.close()
worker.run = run
sys.stdin = io.StringIO(__import__('json').dumps({'root': sys.argv[1]}))
worker.main()
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            env={**os.environ, 'PYTHONPATH': source}, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    files = list(tmp_path.glob('*.json'))
    assert len(files) == 1
    assert json.loads(files[0].read_text())['events'][0]['text'] == 'Stopped'


def test_transcript_seeks_to_audio_start_despite_stt_and_tts_delays(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kufibot_interaction import local_recording
    clock = [10.0]
    monkeypatch.setattr(local_recording, 'time', SimpleNamespace(
        time=lambda: 1000, monotonic=lambda: clock[0], strftime=lambda pattern: 'audio-'))
    recording = SessionRecording(tmp_path)
    recording.begin_utterance('user')
    clock[0] = 11.0
    recording.write('user', b'\x01\x00' * 160)
    clock[0] = 14.0
    recording.record_event('transcript', role='user', text='Batch STT result')
    recording.begin_utterance('assistant')
    clock[0] = 15.0
    recording.record_event('transcript', role='assistant', text='Short TTS response')
    clock[0] = 16.0
    recording.write('assistant', b'\x02\x00' * 160)
    clock[0] = 17.0
    info = recording.close()
    assert [event['offset_ms'] for event in info['events']] == [1000, 6000]
    assert info['duration_sec'] == 7
