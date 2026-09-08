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
