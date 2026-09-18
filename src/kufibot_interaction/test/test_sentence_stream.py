import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kufibot_interaction.sentence_stream import SentenceBuffer
from kufibot_interaction import local_voice_worker as worker


def split(deltas):
    buffer = SentenceBuffer()
    result = []
    for delta in deltas:
        result.extend(buffer.push(delta))
    return result + buffer.finish()


@pytest.mark.parametrize('deltas, expected', [
    (['Merhaba.', ' Nasılsın?', ' İyiyim'], ['Merhaba.', 'Nasılsın?', 'İyiyim']),
    (['Dr.', ' Ayşe geldi. ', 'Prof. Ali de geldi.'], ['Dr. Ayşe geldi.', 'Prof. Ali de geldi.']),
    (['Pi 3.', '14 eder. ', 'Tamam! '], ['Pi 3.14 eder.', 'Tamam!']),
    (['Mr. Smith said “Hello!', '” ', 'Then left.'], ['Mr. Smith said “Hello!”', 'Then left.']),
    (['1. ', 'Merhaba dünya.\n', '2. İyi geceler.'], ['1. Merhaba dünya.', '2. İyi geceler.']),
    (['Tek bir cümle'], ['Tek bir cümle']),
    (['', '  '], []),
    (['A. Yılmaz konuştu. ', 'Bitti.'], ['A. Yılmaz konuştu.', 'Bitti.']),
])
def test_split_stream_without_cutting_numbers_or_abbreviations(deltas, expected):
    assert split(deltas) == expected


def test_sentence_boundary_can_span_deltas():
    buffer = SentenceBuffer()
    assert buffer.push('Hello.') == []  # Could become a decimal/abbreviation.
    assert buffer.push(' ') == ['Hello.']
    assert buffer.finish() == []


def test_first_sentence_starts_tts_before_llm_finishes(monkeypatch):
    spoken = threading.Event()
    sentences = []
    phases = []
    events = []
    reporter = Mock()
    reporter.set_phase.side_effect = phases.append
    monkeypatch.setattr(worker, 'emit', lambda kind, **kw: events.append((kind, kw)))
    def consume(config, voice, source, reporter, **kwargs):
        for sentence in source:
            sentences.append(sentence)
            spoken.set()
        reporter.set_phase('speaking')
    monkeypatch.setattr(worker, 'speak_sentences', consume)
    def deltas(**kwargs):
        yield {'choices': [{'delta': {'content': 'Merhaba. '}}]}
        assert spoken.wait(2), 'TTS must start before requesting the remaining response'
        assert phases == ['thinking']  # Camera stays paused during concurrent LLM/TTS.
        yield {'choices': [{'delta': {'content': 'Nasılsın?'}}]}
    llm = Mock()
    llm.create_chat_completion.side_effect = deltas
    llm.tokenize.return_value = [1, 2]
    result = worker.generate_and_speak({'llm_max_tokens': 160}, llm, [], Mock(), reporter)
    assert result == 'Merhaba. Nasılsın?'
    assert sentences == ['Merhaba.', 'Nasılsın?']
    assert phases == ['thinking', 'synthesizing', 'speaking']
    assert [(kind, value) for kind, value in events if kind == 'compute'] == [
        ('compute', {'active': True}), ('compute', {'active': False})]
    assert ('transcript', {'role': 'assistant', 'text': result}) in events


@pytest.mark.parametrize('suffix', [' ', ''])
def test_prompt_echo_never_reaches_speaker_or_transcript(monkeypatch, suffix):
    events = []
    speaker = Mock()
    monkeypatch.setattr(worker, 'SentenceSpeaker', lambda *args: speaker)
    monkeypatch.setattr(worker, 'emit', lambda kind, **kw: events.append((kind, kw)))
    llm = Mock()
    llm.create_chat_completion.return_value = iter([
        {'choices': [{'delta': {'content': 'Etkin node talimatına göre konuşmayı başlat.' + suffix}}]}])
    llm.tokenize.return_value = [1]
    with pytest.raises(ValueError, match='seslendirme engellendi'):
        worker.generate_and_speak({'llm_max_tokens': 160}, llm, [], Mock(), Mock())
    speaker.put.assert_not_called()
    speaker.close.assert_called_once()
    assert not any(kind == 'transcript' for kind, _ in events)


def test_tts_failure_propagates_without_hanging_queue(monkeypatch):
    failed = threading.Event()
    def consume(config, voice, source, reporter, **kwargs):
        next(source)
        failed.set()
        raise RuntimeError('Piper failure')
    monkeypatch.setattr(worker, 'speak_sentences', consume)
    monkeypatch.setattr(worker, 'emit', Mock())
    def deltas(**kwargs):
        yield {'choices': [{'delta': {'content': 'Hello. '}}]}
        assert failed.wait(2)
        yield {'choices': [{'delta': {'content': 'Next.'}}]}
    llm = Mock()
    llm.create_chat_completion.side_effect = deltas
    with pytest.raises(RuntimeError, match='Piper failure'):
        worker.generate_and_speak({'llm_max_tokens': 160}, llm, [], Mock(), Mock())
    assert not any(t.name == 'local-sentence-tts' for t in threading.enumerate())


def test_workflow_reply_starts_piper_before_decision_finishes(monkeypatch):
    from kufibot_interaction.workflow_inference import make_decider
    from kufibot_interaction.workflows import WorkflowEngine
    spoken, sentences, transcripts = threading.Event(), [], []
    monkeypatch.setattr(worker, 'emit', lambda kind, **kw: transcripts.append((kind, kw)))
    monkeypatch.setattr(worker, 'make_formatter', lambda model: lambda **kw: SimpleNamespace(prompt='formatted'))
    def consume(config, voice, source, reporter, **kwargs):
        for sentence in source:
            sentences.append(sentence)
            spoken.set()
    monkeypatch.setattr(worker, 'speak_sentences', consume)
    def deltas(**kwargs):
        assert kwargs['stream'] is True
        yield {'choices': [{'delta': {'content': 'Merhaba. '}}]}
        assert spoken.wait(2), 'Workflow must not wait for the full answer before TTS'
        yield {'choices': [{'delta': {'content': 'Nasılsın?'}}]}
    model = SimpleNamespace(metadata={}, tokenize=lambda *a, **kw: [1], n_ctx=lambda: 2048,
                            create_chat_completion=deltas)
    decide = make_decider(model, reply_generator=lambda messages: worker.generate_and_speak(
        {'llm_max_tokens': 160}, model, messages, Mock(), Mock()))
    flow = {'schema_version': 1, 'nodes': [{'id': 's', 'type': 'start', 'data': {}},
            {'id': 'a', 'type': 'agent', 'data': {'prompt': 'Selamla.'}}],
            'edges': [{'source': 's', 'target': 'a'}]}
    result = WorkflowEngine(flow, decide, None).turn('Merhaba')
    assert result['text'] == 'Merhaba. Nasılsın?'
    assert sentences == ['Merhaba.', 'Nasılsın?']
    finals = [e for kind, e in transcripts if kind == 'transcript' and e.get('final', True)]
    assert len(finals) == 1


def test_llm_failure_cancels_pending_sentences(monkeypatch):
    consuming = threading.Event()
    stopped = threading.Event()
    def consume(config, voice, source, reporter, **kwargs):
        next(source)
        consuming.set()
        assert kwargs['stopped'].wait(2)
        stopped.set()
    monkeypatch.setattr(worker, 'speak_sentences', consume)
    monkeypatch.setattr(worker, 'emit', Mock())
    def deltas(**kwargs):
        yield {'choices': [{'delta': {'content': 'Hello. '}}]}
        assert consuming.wait(2)
        raise RuntimeError('LLM failure')
    llm = Mock()
    llm.create_chat_completion.side_effect = deltas
    with pytest.raises(RuntimeError, match='LLM failure'):
        worker.generate_and_speak({'llm_max_tokens': 160}, llm, [], Mock(), Mock())
    assert stopped.is_set()
    assert not any(t.name == 'local-sentence-tts' for t in threading.enumerate())


def test_one_playback_stream_for_multiple_sentences(monkeypatch):
    import os
    read_fd, write_fd = os.pipe()
    player = Mock()
    player.stdin = os.fdopen(write_fd, 'wb', buffering=0)
    player.wait.return_value = 0
    player.poll.return_value = 0
    launch = Mock(return_value=player)
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    events = []
    monkeypatch.setattr(worker, 'emit', lambda kind, **kw: events.append((kind, kw)))
    voice = Mock()
    voice.synthesize.side_effect = lambda sentence: iter([SimpleNamespace(
        sample_rate=16000, sample_channels=1, audio_int16_bytes=sentence.encode())])
    try:
        worker.speak_sentences({'speaker': 'null'}, voice, ['one', 'two'], Mock())
        assert os.read(read_fd, 6) == b'onetwo'
    finally:
        os.close(read_fd)
    launch.assert_called_once()
    assert events == [('speaking', {'value': True}), ('speaking', {'value': False})]


def test_sentence_queue_applies_backpressure_without_dropping_text(monkeypatch):
    entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
    received = []
    def consume(config, voice, source, reporter, **kwargs):
        received.append(next(source))
        entered.set()
        assert release.wait(2)
        received.extend(source)
    monkeypatch.setattr(worker, 'speak_sentences', consume)
    speaker = worker.SentenceSpeaker({}, Mock(), Mock())
    producer = None
    try:
        speaker.put('one')
        assert entered.wait(2)
        speaker.put('two')
        speaker.put('three')
        assert speaker.pending.full()
        def produce():
            speaker.put('four')
            submitted.set()
        producer = threading.Thread(target=produce)
        producer.start()
        assert not submitted.wait(.05)
        release.set()
        assert submitted.wait(2)
        producer.join(timeout=2)
        speaker.finish()
    finally:
        release.set()
        speaker.close()
        if producer:
            producer.join(timeout=2)
    assert received == ['one', 'two', 'three', 'four']


def test_empty_response_does_not_open_playback(monkeypatch):
    monkeypatch.setattr(worker, 'emit', Mock())
    launch = Mock()
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    voice, llm = Mock(), Mock()
    llm.tokenize.return_value = []
    llm.create_chat_completion.return_value = iter([{'choices': [{'delta': {'role': 'assistant'}}]}])
    assert worker.generate_and_speak({'llm_max_tokens': 160}, llm, [], voice, Mock()) == ''
    voice.synthesize.assert_not_called()
    launch.assert_not_called()
