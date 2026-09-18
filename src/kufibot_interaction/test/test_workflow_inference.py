from types import SimpleNamespace
import pytest

from kufibot_interaction.workflow_inference import make_decider
from kufibot_interaction.speech_guard import validate_speech


def test_internal_workflow_control_is_rejected():
    with pytest.raises(ValueError, match='seslendirme engellendi'):
        validate_speech('Bu gün görüşme başladı. Etkin node talimatına göre konuşmayı başlat.', [])


def test_persona_and_normal_reply_are_not_instruction_echo():
    validate_speech('Ben Kufi. Bugün neler yaptın?', [{'role': 'system',
        'content': 'Ben Kufi. Kullanıcıya bugün neler yaptığını sor.'}])
    validate_speech('Düğmeye bas ve bekle.', [{'role': 'user', 'content': 'Düğmeye bas ve bekle.'}])


def test_opening_has_no_synthetic_user_instruction(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter',
        lambda model: lambda **kwargs: SimpleNamespace(prompt='formatted'))
    model = SimpleNamespace(metadata={}, n_ctx=lambda: 2048, tokenize=lambda *a, **k: [1])
    messages_seen = []
    def reply(messages):
        messages_seen.extend(messages)
        return 'Merhaba!'
    make_decider(model, reply_generator=reply)({'data': {'prompt': 'Selamla.'}},
        {'user': ''}, [], [], [])
    assert messages_seen[-1] == {'role': 'user', 'content': ''}


def test_ufakzeka_document_answer_is_verbatim_and_does_not_infer():
    model = SimpleNamespace(metadata={'tokenizer.ggml.pre': 'ufakzeka'})
    decide = make_decider(model)
    result = decide({'data': {}}, {'last_result': {'results': [
        {'location': '$["temizlik"]', 'text': '$["temizlik"]: "Robot kuru bezle temizlenir."'}]}}, [], [], [])
    assert result == {'action': 'reply', 'text': 'Belgede şu bilgi yer alıyor: Robot kuru bezle temizlenir.'}


def test_quote_mode_handles_empty_search_without_fabricating():
    decide = make_decider(SimpleNamespace(metadata={}))
    result = decide({'data': {'answer_mode': 'quote'}}, {'last_result': {'results': []}}, [], [], [])
    assert 'bulunamadı' in result['text']


def test_active_node_prompt_is_system_and_user_text_is_not_control_json(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter',
        lambda model: lambda **kwargs: SimpleNamespace(prompt='formatted'))
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        return {'choices': [{'message': {'content': 'Adınız nedir?'}}]}
    model = SimpleNamespace(metadata={}, n_ctx=lambda: 2048, tokenize=lambda *a, **k: [1],
                            create_chat_completion=completion)
    decide = make_decider(model, 'Sen Kufi adlı robotsun.')
    decide({'data': {'message': 'Eski başlangıç talimatı'}}, {'user': 'Merhaba'}, [], [], [])
    decide({'data': {'prompt': 'Kullanıcının adını sor.'}}, {'user': 'Devam'}, [], [], [])
    assert len(calls) == 2  # No unnecessary routing call without tools or exits.
    messages = calls[-1]['messages']
    assert 'Kullanıcının adını sor.' in messages[0]['content']
    assert 'Eski başlangıç talimatı' not in messages[0]['content']
    assert messages[-1] == {'role': 'user', 'content': 'Devam'}
    assert 'Kufibot says' in messages[0]['content']
    assert '<etkin_node_talimati>\nKullanıcının adını sor.' in messages[0]['content']


def test_opening_reply_resembling_a_node_instruction_is_speakable():
    validate_speech('My name is Kufibot.', [{'role': 'system', 'content': 'Start by saying your name is Kufibot.'}])


def test_router_generates_no_discarded_reply_and_delegates_stream_once(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter',
        lambda model: lambda **kwargs: SimpleNamespace(prompt='formatted'))
    calls, streamed = [], []
    def completion(**kwargs):
        calls.append(kwargs)
        return {'choices': [{'message': {'content': '{"action":"reply"}'}}]}
    model = SimpleNamespace(metadata={}, n_ctx=lambda: 2048, tokenize=lambda *a, **k: [1],
                            create_chat_completion=completion)
    def reply(messages):
        streamed.append(messages)
        return 'Merhaba!'
    decide = make_decider(model, reply_generator=reply, reply_max_tokens=160)
    result = decide({'data': {'prompt': 'Selamla.'}}, {'user': 'Merhaba'}, [], [],
                    [{'target': 'next', 'label': 'Devam edince geç.'}])
    assert result == {'action': 'reply', 'text': 'Merhaba!'}
    assert len(calls) == len(streamed) == 1
    variant = calls[0]['response_format']['schema']['oneOf'][0]
    assert set(variant['properties']) == {'action'}
    assert 'text' not in variant['required']


def test_transition_does_not_start_speech(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter',
        lambda model: lambda **kwargs: SimpleNamespace(prompt='formatted'))
    model = SimpleNamespace(metadata={}, n_ctx=lambda: 2048, tokenize=lambda *a, **k: [1],
        create_chat_completion=lambda **kw: {'choices': [{'message': {'content': '{"action":"transition","target":"next"}'}}]})
    streamed = []
    result = make_decider(model, reply_generator=lambda messages: streamed.append(messages))(
        {'data': {}}, {'user': 'devam'}, [], [], [{'target': 'next'}])
    assert result['action'] == 'transition'
    assert not streamed
