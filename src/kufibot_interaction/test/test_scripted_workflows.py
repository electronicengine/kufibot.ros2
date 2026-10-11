import copy
import io
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kufibot_interaction.workflows import WorkflowEngine, validate_graph, render_speech, model_kinds, observation_value
from kufibot_interaction.workflow_routing import SemanticRouter


def graph():
    return {'schema_version': 1, 'id': 'robot', 'workflow_type': 'scripted',
            'settings': {'semantic_threshold': .7},
            'nodes': [dict(id='s', type='start', data={'prompt': 'Merhaba.'}),
                      dict(id='a', type='agent', data={'prompt': 'Hazırım.'}),
                      dict(id='b', type='agent', data={'prompt': 'Gittim.'})],
            'edges': [dict(id='sa', source='s', target='a'),
                      dict(id='ab', source='a', target='b', label='ileri git'),
                      dict(id='ba', source='b', target='a', label='geri dön')]}


def engine(document=None, route=None, tool=None, events=None, **kw):
    return WorkflowEngine(document or graph(), lambda *a: pytest.fail('LLM called'),
                          tool or (lambda *a: pytest.fail('unexpected tool')), (events or Mock()).append if isinstance(events, list) else Mock(),
                          router=route, **kw)


def test_entry_once_no_match_and_return():
    route = Mock(side_effect=[None, {'action': 'transition', 'target': 'b'}, {'action': 'transition', 'target': 'a'}])
    value = engine(route=route)
    assert value.turn('')['text'] == 'Merhaba.\nHazırım.'
    assert not route.called
    assert value.turn('bilinmeyen')['text'] == 'Komutu anlayamadım, tekrar söyler misin?'
    assert value.current == 'a'
    assert value.turn('ileri git')['text'] == 'Gittim.'
    assert value.turn('geri dön')['text'] == 'Hazırım.'
    assert value.context['history'][-1]['user'] == 'geri dön'
    assert route.call_count == 3


def test_semantic_route_uses_only_connected_edges_and_handles_ties():
    flow = graph()
    flow['edges'].append(dict(id='aa', source='a', target='a', label='başka komut'))
    vectors = {'ileri git': [1., 0.], 'başka komut': [1., 0.], 'geri dön': [0., 1.], 'komut': [1., 0.]}
    router = SemanticRouter(flow, 'embedding', factory=lambda _: SimpleNamespace(vector=lambda text: vectors[text]))
    value = engine(flow, route=router)
    value.turn('')
    assert value.turn('komut')['text'].startswith('Komutu anlayamadım')
    assert value.current == 'a'


def test_tool_chain_template_condition_and_no_double_route():
    flow = graph()
    flow['nodes'] += [dict(id='t', type='tool', data={'tool': 'get_sensor_data', 'arguments': {'sensor': 'distance'}}),
                      dict(id='c', type='condition', data={'field': 'last_result.range_m.value', 'operator': 'gt', 'value': 1}),
                      dict(id='e', type='end', data={'prompt': 'Hata.'})]
    flow['nodes'][2]['data']['prompt'] = 'Mesafe {{last_result.range_m.value}} metre.'
    flow['edges'][1]['target'] = 't'
    flow['edges'] += [dict(id='tc', source='t', target='c', sourceHandle='success'),
                      dict(id='te', source='t', target='e', sourceHandle='error'),
                      dict(id='cb', source='c', target='b', sourceHandle='true'),
                      dict(id='ce', source='c', target='e', sourceHandle='false')]
    route = Mock(return_value={'action': 'transition', 'target': 't'})
    tool = Mock(return_value={'range_m': {'status': 'ok', 'value': 2}})
    value = engine(flow, route, tool)
    value.turn('')
    assert value.turn('ileri git')['text'] == 'Mesafe 2 metre.'
    assert route.call_count == tool.call_count == 1
    assert value.context['results']['t']['range_m']['value'] == 2


@pytest.mark.parametrize('template', ['{{last_result.missing}}', '{{__import__(os)}}', '{{results.a}}'])
def test_missing_or_non_scalar_template_fails(template):
    with pytest.raises(ValueError):
        render_speech(template, {'results': {'a': {}}, 'last_result': {}})


def test_plain_speech_is_verbatim():
    assert render_speech('Merhaba, Kufi!\nİkinci satır.', {}) == 'Merhaba, Kufi!\nİkinci satır.'


@pytest.mark.parametrize('status', ['stale', 'unavailable', 'error'])
def test_condition_does_not_treat_unavailable_detection_as_zero(status):
    context = {'last_result': {'perception': {'faces': {'status': status, 'value': 0}}}}
    with pytest.raises(ValueError, match='güncel değil'):
        observation_value(context, 'last_result.perception.faces.value')
    assert observation_value(context, 'last_result.perception.faces.status') == status


def test_infinite_unconditional_loop_is_bounded():
    flow = graph()
    flow['edges'][1]['label'] = flow['edges'][2]['label'] = ''
    with pytest.raises(ValueError, match='16 node'):
        engine(flow).turn('')


def test_resource_call_never_repeats_entry_or_selects_second_action():
    flow = graph()
    flow['nodes'].append(dict(id='r', type='toolResource', data={'tool': 'get_sensor_data', 'arguments': {}, 'trigger_phrases': 'sensörü oku'}))
    flow['edges'].append(dict(id='ra', source='r', target='a'))
    route = Mock(return_value={'action': 'tool', 'tool': 'get_sensor_data', 'arguments': {}})
    tool = Mock(return_value={'status': 'ok'})
    value = engine(flow, route, tool)
    value.turn('')
    assert value.turn('sensörü oku')['text'] == ''
    assert value.current == 'a' and route.call_count == tool.call_count == 1


def test_embedding_error_has_no_llm_fallback():
    route = Mock(side_effect=ValueError('embedding failed'))
    value = engine(route=route)
    value.turn('')
    with pytest.raises(ValueError, match='embedding failed'):
        value.turn('ileri git')


def test_decision_only_accepts_allowed_edge_id():
    flow = graph()
    flow['settings']['decision'] = 'custom'
    flow['nodes'][1]['type'] = 'decision'
    flow['edges'].pop()
    provider = Mock(return_value={'edge_id': 'ab'})
    assert engine(flow, decision_provider=provider).turn('')['text'] == 'Merhaba.\nGittim.'
    assert provider.call_args.args[1]['user'] == ''
    assert provider.call_args.args[2][0]['id'] == 'ab'
    provider.return_value = {'edge_id': 'unconnected'}
    with pytest.raises(ValueError, match='izinli'):
        engine(flow, decision_provider=provider).turn('')


def test_decision_draft_cannot_publish_without_provider():
    flow = graph()
    flow['nodes'][1]['type'] = 'decision'
    assert 'Karar modeli sağlayıcısı seçilmeli' in validate_graph(flow)


def test_types_and_legacy_model_requirements():
    flow = graph()
    assert model_kinds(flow) == ['stt', 'tts', 'embedding']
    flow.pop('workflow_type')
    flow['settings']['llm_enabled'] = False
    assert model_kinds(flow) == ['stt', 'tts']
    flow['workflow_type'] = 'llm'
    assert 'llm' in model_kinds(flow)


def test_scripted_voice_worker_never_loads_chat_model(tmp_path, monkeypatch):
    from kufibot_interaction import local_voice_worker as worker
    import kufibot_interaction.workflow_inference as inference
    import kufibot_interaction.workflow_routing as routing
    flow = graph()
    flow['nodes'][2]['type'] = 'end'
    flow['edges'].pop()
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(disable_telemetry_events=lambda: None))
    class NoChat:
        def __getattr__(self, key):
            pytest.fail('Chat model imported: '+key)
    monkeypatch.setitem(sys.modules, 'llama_cpp', NoChat())
    monkeypatch.setattr(worker, 'create_stt', lambda config: Mock())
    monkeypatch.setattr(worker, 'SileroVad', Mock())
    monkeypatch.setattr(worker, 'load_voice', Mock())
    monkeypatch.setattr(worker, 'Reporter', lambda _: SimpleNamespace(turn=0, mark=Mock(), set_phase=Mock(), close=Mock()))
    monkeypatch.setattr(worker, 'listen_vad', lambda *a: 'ileri git')
    monkeypatch.setattr(inference, 'ToolChannel', Mock(return_value=SimpleNamespace(call=Mock(), turn_id=0)))
    monkeypatch.setattr(routing, 'SemanticRouter', Mock(return_value=Mock(side_effect=lambda *a: {'action': 'transition', 'target': 'b'})))
    speech = []
    monkeypatch.setattr(worker, 'speak_sentences', lambda config, voice, sentences, *a, **kw: speech.extend(sentences))
    monkeypatch.setattr(worker, 'emit', Mock())
    worker.run({'workflow': flow, 'workflow_session_id': 'test', 'embedding': 'embedding', 'language': 'tr', 'recording_root': str(tmp_path)}, max_turns=2)
    assert speech == ['Merhaba.', 'Hazırım.', 'Gittim.']
    assert not inference.ToolChannel.return_value.call.called


def test_text_worker_initializes_router_without_chat_model(monkeypatch):
    from kufibot_interaction import workflow_test_worker as worker
    import kufibot_interaction.workflow_routing as routing
    flow = graph()
    flow['nodes'][2]['type'] = 'end'
    flow['edges'].pop()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'workflow': flow, 'embedding': 'embed', 'text': 'ileri git'})+'\n'))
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setitem(sys.modules, 'llama_cpp', SimpleNamespace(Llama=lambda **kw: pytest.fail('Chat model loaded')))
    monkeypatch.setattr(routing, 'SemanticRouter', Mock(return_value=Mock(side_effect=lambda *a: {'action': 'transition', 'target': 'b'})))
    worker.main()
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert not any(event['type'] == 'error' for event in events)
    assert events[-1]['text'] == 'Gittim.' and events[-1]['ended']
    assert any(event.get('text') == 'Merhaba.\nHazırım.' for event in events)
