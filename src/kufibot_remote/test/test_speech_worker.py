"""The persistent worker loads once and preserves sentence text and events."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

from kufibot_interaction import local_voice_worker
from kufibot_remote import speech_worker


def test_multiple_requests_load_once_and_split_sentences_before_playback(monkeypatch):
    voice = SimpleNamespace(close=Mock())
    load = Mock(return_value=voice)
    spoken, events = [], []
    monkeypatch.setattr(local_voice_worker, 'load_voice', load)
    monkeypatch.setattr(local_voice_worker, 'emit', lambda kind, **data: events.append(kind))
    monkeypatch.setattr(local_voice_worker, 'speak_sentences',
        lambda config, selected, sentences, reporter: spoken.append((config, selected, sentences)))
    requests = [dict(tts='test.onnx', tts_threads=1),
                dict(text='Merhaba! Seni gördüğüme sevindim.', speaker='null'),
                dict(text='Dr. Kufi ile görüş.', speaker='other')]
    monkeypatch.setattr(speech_worker.sys, 'stdin', io.StringIO(
        ''.join(json.dumps(request) + '\n' for request in requests)))
    speech_worker.main()
    load.assert_called_once()
    assert spoken[0][2] == ['Merhaba!', 'Seni gördüğüme sevindim.']
    assert spoken[1][2] == ['Dr. Kufi ile görüş.']
    assert spoken[1][0]['speaker'] == 'other'
    assert all(call[1] is voice for call in spoken)
    assert events == ['ready', 'done', 'done']
    voice.close.assert_called_once()
