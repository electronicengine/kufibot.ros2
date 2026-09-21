from collections import Counter
from types import SimpleNamespace

import pytest

from kufibot_interaction.workflow_routing import SemanticRouter
from kufibot_interaction.workflow_inference import make_decider
from kufibot_interaction.workflows import WorkflowEngine, validate_graph


def graph():
    return {'schema_version': 1, 'settings': {}, 'nodes': [
        {'id': 'start', 'type': 'start', 'data': {}},
        {'id': 'a', 'type': 'agent', 'data': {'prompt': 'Eski komut'}},
        {'id': 'b', 'type': 'agent', 'data': {'prompt': 'Yeni komut'}}],
        'edges': [{'source': 'start', 'target': 'a'}, {'source': 'a', 'target': 'b', 'label': 'devam'}]}


def router(document, vectors, events=None):
    calls = Counter()
    class Embedder:
        def __init__(self, path):
            assert path == 'embedding.gguf'
        def vector(self, text):
            calls[text] += 1
            return vectors[text]
        def close(self):
            calls['closed'] += 1
    value = SemanticRouter(document, 'embedding.gguf', factory=Embedder,
                           notify=(events if events is not None else []).append)
    return value, calls


@pytest.mark.parametrize('score,expected', [(.69, False), (.70, True), (.71, True)])
def test_cosine_threshold_boundary(score, expected):
    flow = graph()
    route, _ = router(flow, {'devam': [1, 0], 'soru': [score, (1 - score ** 2) ** .5]})
    result = route(flow['nodes'][1], {'user': 'soru'})
    assert bool(result) is expected
    if expected:
        assert result == {'action': 'transition', 'target': 'b'}
    route.close()


def test_transition_uses_target_prompt_once_no_fake_tool_events(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter',
                        lambda _: lambda **kwargs: SimpleNamespace(prompt='formatted'))
    flow = graph()
    flow['edges'].append({'source': 'b', 'target': 'a', 'label': 'devam'})
    route, counts = router(flow, {'devam': [1, 0]})
    messages, events = [], []
    model = SimpleNamespace(n_ctx=lambda: 2048, tokenize=lambda *a, **kw: [1])
    def reply(value):
        messages.append(value)
        return 'Yanıt.'
    engine = WorkflowEngine(flow, make_decider(model, 'Eklenmemeli', reply_generator=reply),
                            lambda *args: pytest.fail('transition must not call a tool'), events.append, router=route)
    engine.turn('')  # The start node must answer too.
    assert engine.turn('devam')['text'] == 'Yanıt.'
    assert engine.current == 'a'  # First agent reply cannot be skipped.
    assert messages[-1][0] == {'role': 'system', 'content': 'Eski komut'}
    assert messages[-1][-1] == {'role': 'user', 'content': 'devam'}
    assert engine.context['history'][-1]['user'] == 'devam'
    assert engine.turn('devam')['text'] == 'Yanıt.'
    assert engine.current == 'b'
    assert messages[-1][0] == {'role': 'system', 'content': 'Yeni komut'}
    assert messages[-1][-1] == {'role': 'user', 'content': 'devam'}
    assert all(sum(m['role'] == 'system' for m in call) == 1 for call in messages[1:])
    assert not any(m['content'] == 'Eski komut' for m in messages[-1])
    assert engine.context['history'][-1]['user'] == 'devam'
    assert not any(e['type'].startswith('tool') for e in events)
    engine.turn('devam')
    assert engine.current == 'a'
    assert counts['devam'] == 1
    route.close()
    assert counts['closed'] == 1


def test_highest_scoring_attached_tool_uses_configured_arguments_and_ref():
    flow = graph()
    flow['nodes'] += [{'id': 'sensor', 'type': 'toolResource', 'data': {
        'tool': 'get_sensor_data', 'trigger_phrases': 'pil durumu\nbatarya',
        'arguments': {'sensor': 'battery', 'question': {'$ref': 'user'}}}}]
    flow['edges'].append({'source': 'sensor', 'target': 'a'})
    route, _ = router(flow, {'devam': [0, 1], 'pil durumu': [1, 0], 'batarya': [.9, .1], 'şarj kaç': [1, 0]})
    calls = []
    engine = WorkflowEngine(flow, lambda *a: {'action': 'reply', 'text': '12 volt.'},
                            lambda name, params: calls.append((name, params)) or {'voltage': 12}, router=route)
    engine.turn('')
    engine.turn('şarj kaç')
    assert calls == [('get_sensor_data', {'sensor': 'battery', 'question': 'şarj kaç'})]
    route.close()


def test_empty_opening_unlabelled_exits_and_unconfigured_tools_do_not_match():
    flow = graph()
    route, calls = router(flow, {})
    assert route(flow['nodes'][1], {'user': ''}) is None
    assert not calls
    flow['edges'][1]['label'] = ''
    flow['nodes'].append({'id': 'sensor', 'type': 'toolResource', 'data': {'tool': 'get_robot_status'}})
    flow['edges'].append({'source': 'sensor', 'target': 'a'})
    route, calls = router(flow, {})
    assert route(flow['nodes'][1], {'user': 'durum'}) is None
    assert not calls


def test_equal_scores_do_not_choose_an_arbitrary_action():
    flow = graph()
    flow['edges'].append({'source': 'a', 'target': 'a', 'label': 'kal'})
    events = []
    route, _ = router(flow, {'devam': [1, 0], 'kal': [1, 0], 'soru': [1, 0]}, events)
    assert route(flow['nodes'][1], {'user': 'soru'}) is None
    assert events[-1]['ambiguous'] and not events[-1]['matched']
    route.close()


def test_knowledge_only_searches_matched_collection():
    flow = graph()
    flow['nodes'] += [{'id': 'k', 'type': 'knowledge', 'data': {
        'collection_id': 'manual', 'trigger_phrases': ['temizlik']}}]
    flow['edges'].append({'source': 'k', 'target': 'a'})
    route, _ = router(flow, {'devam': [0, 1], 'temizlik': [1, 0]})
    calls = []
    engine = WorkflowEngine(flow, lambda *a: {'action': 'reply', 'text': 'Kuru bez.'},
        lambda name, args: calls.append((name, args)) or {'results': []}, router=route)
    engine.turn('')
    engine.turn('temizlik')
    assert calls == [('search_documents', {'query': 'temizlik', 'collection_ids': ['manual'], 'top_k': 4})]
    route.close()


@pytest.mark.parametrize('threshold', [0, -1, 1.1, float('nan'), True, '0.7'])
def test_invalid_threshold_rejected_before_execution(threshold):
    flow = graph()
    flow['settings']['semantic_threshold'] = threshold
    assert any('eşik' in error for error in validate_graph(flow))
    with pytest.raises(ValueError, match='eşik'):
        router(flow, {})


def test_embedding_failure_never_falls_back_to_llm_routing():
    flow = graph()
    route, _ = router(flow, {'devam': [1, 0], 'bozuk': [float('nan'), 0]})
    replies = []
    engine = WorkflowEngine(flow, lambda *a: replies.append(1) or {'action': 'reply', 'text': 'Yanıt'}, None, router=route)
    engine.turn('')
    engine.turn('ilk mesaj')
    assert len(replies) == 2
    with pytest.raises(ValueError, match='embedding'):
        engine.turn('bozuk')
    assert len(replies) == 2
    route.close()


@pytest.mark.parametrize('first_message', ['selam', 'Bugün ne yapacaksın?'])
def test_conditional_start_waits_for_own_reply_and_then_only_matching_input(first_message):
    flow = graph()
    condition = 'eğer bugün ne yapacağın sorulursa diğer düğüme geç'
    flow['nodes'][0]['data']['prompt'] = 'Kullanıcıyı selamla.'
    flow['edges'][0]['label'] = condition
    # A third agent verifies that the same utterance cannot skip the target.
    flow['edges'][1]['label'] = condition
    route, counts = router(flow, {condition: [1, 0], 'selam': [0, 1], 'Bugün ne yapacaksın?': [1, 0]})
    replies, events = [], []
    def reply(node, context, *args):
        replies.append(node['id'])
        return {'action': 'reply', 'text': node['data']['prompt']}
    engine = WorkflowEngine(flow, reply, None, events.append, router=route)
    engine.turn(first_message)
    assert engine.current == 'start' and replies == ['start']
    assert not counts  # No embedding transition inference before the first reply.
    assert not any(e['type'] == 'transition' for e in events)
    engine.turn('selam')
    assert engine.current == 'start' and replies == ['start', 'start']
    engine.turn('Bugün ne yapacaksın?')
    assert engine.current == 'a' and replies == ['start', 'start', 'a']
    engine.turn('selam')
    assert engine.current == 'a'
    engine.turn('Bugün ne yapacaksın?')
    assert engine.current == 'b' and replies[-1] == 'b'
    route.close()


def test_failed_or_empty_reply_does_not_unlock_transition():
    flow = graph()
    flow['edges'][0]['label'] = 'devam'
    route, counts = router(flow, {'devam': [1, 0]})
    outcomes = iter([RuntimeError('LLM hatası'), '', 'Hazır', 'Hedef'])
    def reply(*args):
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return {'action': 'reply', 'text': value}
    engine = WorkflowEngine(flow, reply, None, router=route)
    with pytest.raises(RuntimeError, match='LLM'):
        engine.turn('devam')
    assert engine.current == 'start' and not engine.has_replied
    with pytest.raises(ValueError, match='boş workflow yanıtı'):
        engine.turn('devam')
    assert engine.current == 'start' and not engine.has_replied
    engine.turn('devam')
    assert engine.current == 'start' and engine.has_replied and not counts
    engine.turn('devam')
    assert engine.current == 'a'
    route.close()


def test_each_visit_resets_reply_gate_even_for_previously_answered_node():
    flow = graph()
    flow['edges'].append({'source': 'b', 'target': 'a', 'label': 'devam'})
    route, _ = router(flow, {'devam': [1, 0]})
    require_llm = []
    def reply(node, context, *args):
        require_llm.append((node['id'], context['_require_llm']))
        return {'action': 'reply', 'text': 'Yanıt'}
    engine = WorkflowEngine(flow, reply, None, router=route)
    for _ in range(5):
        engine.turn('devam')
    assert require_llm == [('start', True), ('a', True), ('b', True), ('a', True), ('b', True)]
    route.close()


def test_blocked_exit_does_not_hide_attached_tool_on_first_agent_turn():
    flow = graph()
    flow['nodes'].append({'id': 'sensor', 'type': 'toolResource', 'data': {
        'tool': 'get_sensor_data', 'arguments': {'sensor': 'battery'}, 'trigger_phrases': 'pil'}})
    flow['edges'].append({'source': 'sensor', 'target': 'a'})
    route, _ = router(flow, {'devam': [1, 0], 'pil': [.9, .1], 'soru': [1, 0]})
    calls = []
    engine = WorkflowEngine(flow, lambda *a: {'action': 'reply', 'text': 'Yanıt'},
                            lambda *a: calls.append(a) or {}, router=route)
    engine.turn('')
    engine.turn('soru')
    assert engine.current == 'a'
    assert calls == [('get_sensor_data', {'sensor': 'battery'})]
    route.close()
