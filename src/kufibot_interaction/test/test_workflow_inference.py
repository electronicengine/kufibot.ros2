from types import SimpleNamespace
import pytest

from kufibot_interaction.workflow_inference import make_decider
from kufibot_interaction.speech_guard import validate_speech


@pytest.fixture
def model(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter',
        lambda model: lambda **kwargs: SimpleNamespace(prompt='formatted'))
    calls = []
    def completion(**kwargs):
        calls.append(kwargs)
        return {'choices': [{'message': {'content': 'Adınız nedir?'}}]}
    return SimpleNamespace(metadata={}, n_ctx=lambda: 2048, tokenize=lambda *a, **k: [1],
                           create_chat_completion=completion, calls=calls)


def test_internal_workflow_control_is_rejected():
    with pytest.raises(ValueError, match='seslendirme engellendi'):
        validate_speech('Bu gün görüşme başladı. Etkin node talimatına göre konuşmayı başlat.', [])


def test_persona_and_normal_reply_are_not_instruction_echo():
    validate_speech('Ben Kufi. Bugün neler yaptın?', [{'role': 'system',
        'content': 'Ben Kufi. Kullanıcıya bugün neler yaptığını sor.'}])
    validate_speech('Düğmeye bas ve bekle.', [{'role': 'user', 'content': 'Düğmeye bas ve bekle.'}])


def test_opening_has_no_synthetic_user_instruction(model):
    make_decider(model)({'data': {'prompt': 'Selamla.'}}, {'user': ''}, [], [], [])
    assert model.calls[0]['messages'] == [
        {'role': 'system', 'content': 'Selamla.'},
        {'role': 'user', 'content': ''}]


def test_quote_mode_is_explicit_and_has_no_static_spoken_prefix(model):
    model.metadata = {'tokenizer.ggml.pre': 'ufakzeka'}
    context = {'user': 'Nasıl temizlenir?', 'last_result': {'results': [
        {'location': '$["temizlik"]', 'text': '$["temizlik"]: "Robot kuru bezle temizlenir."'}]}}
    result = make_decider(model)({'data': {'answer_mode': 'quote'}}, context, [], [], [])
    assert result == {'action': 'reply', 'text': 'Robot kuru bezle temizlenir.'}
    assert not model.calls
    make_decider(model)({'data': {'prompt': 'Kısa yanıt ver.'}}, context, [], [], [])
    assert len(model.calls) == 1  # No tokenizer-dependent instruction/answer policy.


def test_empty_search_uses_node_prompt_without_static_answer(model):
    result = make_decider(model)({'data': {'answer_mode': 'quote', 'prompt': 'Kayıt yoksa söyle.'}},
                                {'last_result': {'results': []}}, [], [], [])
    assert result['text'] == 'Adınız nedir?'
    assert model.calls[0]['messages'] == [
        {'role': 'system', 'content': 'Kayıt yoksa söyle.'},
        {'role': 'user', 'content': '\n\n{"tool_result": {"results": []}}'}]


def test_active_node_is_system_and_real_user_is_preserved_without_control_schema(model):
    decide = make_decider(model, 'BU ORTAK TALİMAT EKLENMEMELİ', 'en')
    decide({'data': {'message': 'Eski başlangıç talimatı'}}, {'user': 'Merhaba'}, [], [], [])
    decide({'data': {'prompt': 'Kullanıcının adını sor.'}}, {'user': 'Devam'},
           ['get_robot_status'], [], [{'target': 'next', 'label': 'Devam'}])
    assert len(model.calls) == 2
    assert model.calls[0]['messages'] == [
        {'role': 'system', 'content': 'Eski başlangıç talimatı'},
        {'role': 'user', 'content': 'Merhaba'}]
    assert model.calls[-1]['messages'] == [
        {'role': 'system', 'content': 'Kullanıcının adını sor.'},
        {'role': 'user', 'content': 'Devam'}]
    assert all('response_format' not in call and 'tools' not in call for call in model.calls)


def test_stream_once_without_router_llm_call(model):
    streamed = []
    def reply(messages):
        streamed.append(messages)
        return 'Merhaba!'
    result = make_decider(model, reply_generator=reply)({'data': {'prompt': 'Selamla.'}},
        {'user': 'Merhaba'}, ['get_robot_status'], [], [{'target': 'next', 'label': 'Devam'}])
    assert result == {'action': 'reply', 'text': 'Merhaba!', 'request_text': 'Merhaba'}
    assert not model.calls
    assert streamed == [[{'role': 'system', 'content': 'Selamla.'},
                         {'role': 'user', 'content': 'Merhaba'}]]


def test_evidence_is_data_added_to_entry_request(model):
    make_decider(model)({'data': {'prompt': 'Sensör sonucunu açıkla.'}},
        {'user': 'Pil?', 'last_result': {'voltage': 12}}, [], [], [])
    assert model.calls[0]['messages'] == [
        {'role': 'system', 'content': 'Sensör sonucunu açıkla.'},
        {'role': 'user', 'content': 'Pil?\n\n{"tool_result": {"voltage": 12}}'}]


def test_context_budget_keeps_entire_node_prompt(model):
    model.n_ctx = lambda: 100
    model.tokenize = lambda *args, **kwargs: list(range(500))
    with pytest.raises(ValueError, match='bağlamını aşıyor'):
        make_decider(model)({'data': {'prompt': 'Uzun talimat'}}, {'user': 'merhaba'}, [], [], [])
    assert not model.calls


def test_first_reply_requires_llm_even_when_node_configures_quotes(model):
    result = make_decider(model)({'data': {'prompt': 'Belgeye göre yanıtla.', 'answer_mode': 'quote'}},
        {'_require_llm': True, 'user': 'pil?', 'last_result': {'results': [{'text': '12 volt'}]}}, [], [], [])
    assert result['text'] == 'Adınız nedir?'
    assert len(model.calls) == 1
    assert model.calls[0]['messages'][0] == {'role': 'system', 'content': 'Belgeye göre yanıtla.'}
    assert model.calls[0]['messages'][-1]['content'].startswith('pil?\n\n')


def test_followup_uses_user_message_and_actual_entry_history(model):
    make_decider(model)({'data': {'prompt': 'Adının Kufi olduğunu söyle.'}},
        {'_require_llm': False, 'user': 'Nasılsın?', 'history': [
            {'user': 'Adın ne?', 'assistant': 'Ben Kufi.'}]}, [], [], [])
    assert model.calls[0]['messages'] == [
        {'role': 'system', 'content': 'Adının Kufi olduğunu söyle.'},
        {'role': 'user', 'content': 'Adın ne?'},
        {'role': 'assistant', 'content': 'Ben Kufi.'},
        {'role': 'user', 'content': 'Nasılsın?'}]


def test_empty_entry_instruction_uses_real_utterance(model):
    make_decider(model)({'data': {'prompt': ''}},
        {'_require_llm': True, 'user': 'Merhaba'}, [], [], [])
    assert model.calls[0]['messages'] == [{'role': 'user', 'content': 'Merhaba'}]


def test_context_trimming_preserves_system_and_current_user(model, monkeypatch):
    candidates = []
    def format_messages(messages):
        candidates.append(messages)
        return SimpleNamespace(prompt='x' * (900 if len(messages) > 2 else 20))
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter', lambda _: format_messages)
    model.n_ctx = lambda: 400
    model.tokenize = lambda value, **kwargs: list(value)
    make_decider(model)({'data': {'prompt': 'Komut bütünüyle korunmalı.'}},
        {'user': 'Güncel soru', 'history': [{'user': 'Eski soru', 'assistant': 'Eski yanıt'}]}, [], [], [])
    assert len(candidates) == 2
    assert model.calls[0]['messages'] == [
        {'role': 'system', 'content': 'Komut bütünüyle korunmalı.'},
        {'role': 'user', 'content': 'Güncel soru'}]
