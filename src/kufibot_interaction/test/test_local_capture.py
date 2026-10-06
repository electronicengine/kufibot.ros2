import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from kufibot_interaction import local_voice_worker as worker


def recorder(monkeypatch, script):
    original = subprocess.Popen
    processes = []

    def launch(_args, **kwargs):
        process = original([sys.executable, '-c', script], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    return processes


def test_ready_requires_pcm_and_normal_stop_hides_arecord_eintr(monkeypatch, capfd):
    processes = recorder(monkeypatch, '''
import os, signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: (sys.stderr.write('read error: Interrupted system call\\n'), sys.exit(0)))
os.write(1, bytes(4000))
time.sleep(10)
''')
    recognizer = Mock()
    recognizer.AcceptWaveform.return_value = True
    recognizer.Result.return_value = json.dumps({'text': 'hello'})
    events = Mock()
    assert worker.listen({'mic': 'test'}, recognizer, events) == 'hello'
    events.assert_called_once_with('ready')
    assert processes[0].poll() is not None
    assert 'Interrupted system call' not in capfd.readouterr().err


def test_real_capture_failure_preserves_alsa_detail(monkeypatch):
    recorder(monkeypatch, "import sys; sys.stderr.write('audio open error: Device or resource busy\\n')")
    events = Mock()
    with pytest.raises(RuntimeError, match='Device or resource busy'):
        worker.listen({'mic': 'test'}, Mock(), events)
    events.assert_not_called()


def test_stalled_capture_times_out_and_reaps_process(monkeypatch):
    processes = recorder(monkeypatch, 'import time; time.sleep(10)')
    monkeypatch.setattr(worker.select, 'select', lambda *_: ([], [], []))
    with pytest.raises(RuntimeError, match='PCM'):
        worker.listen({'mic': 'test'}, Mock())
    assert processes[0].poll() is not None


def test_local_capture_preserves_fragmented_pcm_under_stt_load(monkeypatch):
    import time
    from kufibot_interaction.local_voice_runtime import DEFAULTS
    recorder(monkeypatch, '''
import os, time
pcm = b''.join(bytes([index, 0]) * 512 for index in range(1, 11))
for start in range(0, len(pcm), 377):
    os.write(1, pcm[start:start + 377])
    time.sleep(.002)
time.sleep(10)
''')
    received, archived = [], []
    backend = Mock(spec=['reset', 'feed', 'finish'])
    def feed(pcm):
        time.sleep(.04)
        received.append(pcm)
    backend.feed.side_effect = feed
    backend.finish.return_value = 'test'
    archive = Mock()
    archive.write.side_effect = lambda role, pcm: archived.append(pcm)
    config = {**DEFAULTS, 'mic': 'test', 'vad_min_speech_ms': 32,
              'max_utterance_sec': .32}
    assert worker.listen_vad(config, backend, Mock(return_value=.99), Mock(), archive) == 'test'
    expected = [bytes([index, 0]) * 512 for index in range(1, 11)]
    assert received == archived == expected


def test_wake_diagnostics_show_vad_acceptance_and_final_stt(monkeypatch):
    from kufibot_interaction.local_voice_runtime import DEFAULTS
    recorder(monkeypatch, 'import os, time; os.write(1, bytes(1024 * 25)); time.sleep(10)')
    probabilities = iter([.9] * 3 + [.01] * 22)
    vad = Mock(side_effect=lambda _: next(probabilities))
    backend = Mock(spec=['reset', 'feed', 'finish'])
    backend.finish.return_value = 'kafe'
    events = []
    monkeypatch.setattr(worker, 'emit', lambda kind, **data: events.append((kind, data)))
    result = worker.listen_vad({**DEFAULTS, 'mic': 'test', 'wake_diagnostics': True},
                               backend, vad, Mock())
    assert result == 'kafe'
    messages = [data['message'] for kind, data in events if kind == 'diagnostic']
    assert any('Wake VAD accepted: speech_ms=96' in message for message in messages)
    assert any('Wake STT input:' in message for message in messages)
    assert "Wake STT result: text='kafe'" in messages
