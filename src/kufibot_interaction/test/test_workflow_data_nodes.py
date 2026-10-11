import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kufibot_interaction.workflows import WorkflowEngine, validate_graph, render_speech
from kufibot_interaction.workflow_resources import resource_arguments
from kufibot_interaction.workflow_inference import make_resource_selector
from kufibot_interaction.workflow_values import read_value, resolve


def graph(workflow_type='scripted', query=None):
    return {'schema_version':1, 'workflow_type':workflow_type, 'settings':{}, 'nodes':[
        {'id':'start','type':'start','data':{'prompt':'Başlıyoruz.', 'output':{'mode':'json','value':{'soru':'pil'}}}},
        {'id':'search','type':'knowledgeStep','data':{'collection_id':'manual','query':query if query is not None else {'$ref':'user'},'top_k':4}},
        {'id':'condition','type':'condition','data':{'field':'last_output.found','operator':'eq','value':True}},
        {'id':'reply','type':'end','data':{'prompt':'{{outputs.search.results.0.text}}'}},
        {'id':'empty','type':'end','data':{'prompt':'Bilgi yok.'}},
        {'id':'error','type':'end','data':{'prompt':'Arama hatası: {{last_result.error}}'}},
    ], 'edges':[
        {'source':'start','target':'search'}, {'source':'search','target':'condition','sourceHandle':'success'},
        {'source':'search','target':'error','sourceHandle':'error'},
        {'source':'condition','target':'reply','sourceHandle':'true'}, {'source':'condition','target':'empty','sourceHandle':'false'},
    ]}


@pytest.mark.parametrize('query', ['pil', {'$ref':'user'}, {'$ref':'last_output.soru'}, {'$ref':'outputs.start.soru'}])
def test_knowledge_step_input_output_condition_and_tts(query):
    calls, events = [], []
    def search(name, params):
        calls.append((name, params))
        return {'status':'ok','results':[{'text':'Pil 12 volt.','filename':'manual.json','location':'pil','score':.9}]}
    engine = WorkflowEngine(graph(query=query), None, search, events.append)
    answer = engine.turn('pil')
    assert answer['text'] == 'Başlıyoruz.\nPil 12 volt.'
    assert calls == [('search_documents', {'query':'pil','collection_ids':['manual'],'top_k':4})]
    assert engine.context['outputs']['search']['found'] is True
    assert engine.context['outputs']['condition']['matched'] is True
    assert engine.context['outputs']['reply'] == {'text':'Pil 12 volt.'}
    assert any(e['type'] == 'node_output' and e['node_id'] == 'search' for e in events)
    assert answer['sources'][0]['filename'] == 'manual.json'


def test_no_results_is_success_with_found_false():
    engine = WorkflowEngine(graph(), None, lambda *args:{'status':'ok','results':[]})
    assert engine.turn('pil')['text'] == 'Başlıyoruz.\nBilgi yok.'
    assert engine.context['outputs']['search'] == {'status':'ok','results':[],'found':False,'text':''}


@pytest.mark.parametrize('query', ['', {'$ref':'outputs.missing.text'}, {'$ref':'last_output.unknown'}, {'$ref':'last_output'}])
def test_bad_query_uses_error_exit_without_search(query):
    call = Mock()
    engine = WorkflowEngine(graph(query=query), None, call)
    assert 'Arama hatası:' in engine.turn('pil')['text']
    call.assert_not_called()
    assert engine.context['outputs']['search']['status'] == 'error'


def test_embedding_failure_is_a_step_error():
    def fail(*args):
        raise ValueError('Embedding modeli değişti; yeniden indeksleyin')
    engine = WorkflowEngine(graph(), None, fail)
    assert 'Embedding modeli değişti' in engine.turn('pil')['text']


def test_custom_json_and_text_outputs_use_fields_without_changing_raw_result():
    flow = graph()
    flow['nodes'][1]['data']['output'] = {'mode':'json','value':{'found':{'$ref':'result.found'}, 'cevap':{'$ref':'result.results.0.text'}, 'etiket':'kılavuz'}}
    flow['nodes'][3]['data']['prompt'] = '{{outputs.search.cevap}}'
    engine = WorkflowEngine(flow, None, lambda *args:{'status':'ok','results':[{'text':'12 volt'}]})
    assert engine.turn('pil')['text'].endswith('12 volt')
    assert engine.context['outputs']['search'] == {'found':True,'cevap':'12 volt','etiket':'kılavuz'}
    assert engine.context['results']['search']['results'][0]['text'] == '12 volt'


def test_invalid_projection_uses_error_exit():
    flow = graph()
    flow['nodes'][1]['data']['output'] = {'mode':'text','value':{'$ref':'result.missing'}}
    engine = WorkflowEngine(flow, None, lambda *args:{'status':'ok','results':[]})
    assert 'Veri alanı bulunamadı' in engine.turn('pil')['text']


def test_llm_step_chain_passes_output_to_reply_without_selector():
    visited = []
    def reply(node, context, *args):
        visited.append((node['id'], copy.deepcopy(context.get('last_output'))))
        return {'action':'reply','text':'Merhaba' if node['type'] == 'start' else 'Pil 12 volt'}
    engine = WorkflowEngine(graph('llm', query='pil'), reply, lambda *args:{'status':'ok','results':[{'text':'12 volt'}]})
    assert engine.turn('')['text'] == 'Merhaba\nPil 12 volt'
    assert visited[-1][0] == 'reply'
    assert visited[-1][1]['matched'] is True


def resource_flow():
    return {'schema_version':1,'workflow_type':'llm','settings':{},'nodes':[
        {'id':'s','type':'start','data':{}}, {'id':'a','type':'agent','data':{}},
        {'id':'k','type':'knowledge','data':{'collection_id':'manual'}},
        {'id':'t','type':'toolResource','data':{'tool':'move_base','arguments':{'direction':'forward','speed_mps':.25,'duration_sec':1},'llm_parameters':['direction']}},
    ],'edges':[{'source':'s','target':'a'}, {'source':'k','target':'a','targetHandle':'resource'}, {'source':'t','target':'a','targetHandle':'resource'}]}


def test_llm_resource_selection_query_and_separate_outputs():
    selections = iter([{'resource_id':'k','arguments':{'query':'batarya voltajı'}}, None])
    calls = []
    def select(node, context, resources):
        assert {r['id'] for r in resources} == {'k','t'}
        return next(selections)
    engine = WorkflowEngine(resource_flow(), lambda *args:{'action':'reply','text':'12 volt'},
        lambda name, args:calls.append((name,args)) or {'status':'ok','results':[{'text':'12 volt'}]}, resource_selector=select)
    engine.turn('')
    assert engine.turn('Pil kaç volt?')['text'] == '12 volt'
    assert calls == [('search_documents', {'query':'batarya voltajı','collection_ids':['manual'],'top_k':4})]
    assert engine.context['outputs']['k']['results'][0]['text'] == '12 volt'
    assert engine.context['outputs']['a'] == {'text':'12 volt'}


@pytest.mark.parametrize('choice', [
    {'resource_id':'unattached','arguments':{}},
    {'resource_id':'k','arguments':{'collection_ids':['secret']}},
    {'resource_id':'t','arguments':{'speed_mps':.4}},
    {'resource_id':'t','arguments':{'direction':'sideways'}},
    {'resource_id':'k','arguments':{'query':{'$ref':'outputs.secret'}}},
    {'resource_id':'k','arguments':{'query':'x' * 4001}},
])
def test_model_cannot_call_unattached_or_change_locked_parameters(choice):
    call = Mock()
    engine = WorkflowEngine(resource_flow(), lambda *args:{'action':'reply','text':'Hazır'}, call, resource_selector=lambda *args:choice)
    engine.turn('')
    with pytest.raises(ValueError):
        engine.turn('git')
    call.assert_not_called()


def test_model_editable_fields_merge_fixed_motor_values():
    resource = resource_flow()['nodes'][3]
    assert resource_arguments(resource, {'direction':'backward'}, {}) == {'direction':'backward','speed_mps':.25,'duration_sec':1}


@pytest.mark.parametrize('response', ['not json', '{}', '{"resource_id":"missing","arguments":{}}', '{"resource_id":null,"arguments":{"query":"x"}}'])
def test_selector_json_failures_are_visible(monkeypatch, response):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter', lambda model:lambda **kwargs:SimpleNamespace(prompt='prompt'))
    model = SimpleNamespace(tokenize=lambda *args,**kwargs:[1], n_ctx=lambda:2048,
        create_chat_completion=lambda **kwargs:{'choices':[{'message':{'content':response}}]})
    select = make_resource_selector(model)
    with pytest.raises(ValueError):
        select({'data':{}}, {'user':'pil'}, [{'id':'k','parameters':{'query':{'type':'string'}}}])


def test_json_selector_uses_schema_and_never_speech_generator(monkeypatch):
    monkeypatch.setattr('kufibot_interaction.local_voice_worker.make_formatter', lambda model:lambda **kwargs:SimpleNamespace(prompt='prompt'))
    completion = Mock(return_value={'choices':[{'message':{'content':'{"resource_id":"k","arguments":{"query":"pil"}}'}}]})
    model = SimpleNamespace(tokenize=lambda *args,**kwargs:[1], n_ctx=lambda:2048, create_chat_completion=completion)
    select = make_resource_selector(model)
    assert select({'data':{}}, {'user':'pil'}, [{'id':'k','parameters':{'query':{'type':'string', 'maxLength':4000}}}])['arguments']['query'] == 'pil'
    schema = completion.call_args.kwargs['response_format']['schema']
    assert schema['anyOf'][1]['properties']['arguments']['additionalProperties'] is False
    assert 'maxLength' not in schema['anyOf'][1]['properties']['arguments']['properties']['query']


def test_reference_array_missing_freshness_and_scalar_template():
    context = {'outputs':{'k':{'results':[{'text':'12 volt'}]},'bad':{'status':'stale','value':12}}, 'last_output':'hazır'}
    assert render_speech('{{outputs.k.results.0.text}} {{last_output}}', context) == '12 volt hazır'
    with pytest.raises(ValueError, match='bulunamadı'):
        resolve({'$ref':'outputs.k.results.1.text'}, context)
    with pytest.raises(ValueError, match='güncel değil'):
        read_value(context, 'outputs.bad.value')
    assert read_value(context, 'outputs.bad.status') == 'stale'
    with pytest.raises(ValueError):
        resolve({'$ref':'__import__.os'}, context)


@pytest.mark.parametrize('output', [{'mode':'text','value':{'$ref':'user'}}, {'mode':'user'}])
def test_outputs_survive_turns_update_on_return_and_are_session_local(output):
    flow = {'schema_version':1,'workflow_type':'scripted','settings':{},'nodes':[
        {'id':'s','type':'start','data':{'prompt':'Giriş'}},
        {'id':'a','type':'agent','data':{'prompt':'İlk', 'output':output}},
        {'id':'b','type':'agent','data':{'prompt':'İkinci'}},
    ],'edges':[{'source':'s','target':'a'}, {'source':'a','target':'b','label':'devam'}, {'source':'b','target':'a','label':'dön'}]}
    def route(node, context):
        return {'action':'transition','target':'b' if node['id'] == 'a' else 'a'}
    engine = WorkflowEngine(flow, None, None, router=route)
    engine.turn('ilk')
    engine.turn('devam')
    assert engine.context['outputs']['a'] == ({'text':'ilk'} if output['mode'] == 'user' else 'ilk')
    if output['mode'] == 'user':
        assert render_speech('{{outputs.a.text}}', engine.context) == 'ilk'
    engine.turn('dön')
    assert engine.context['outputs']['a'] == ({'text':'dön'} if output['mode'] == 'user' else 'dön')
    other = WorkflowEngine(flow, None, None, router=route)
    assert other.context['outputs'] == {}


def test_resource_connections_and_step_exits_are_validated():
    flow = resource_flow()
    assert not validate_graph(flow)
    flow['edges'][1]['target'] = 't'
    assert any('Kaynaklar' in e for e in validate_graph(flow))
    flow = graph()
    flow['edges'][0]['targetHandle'] = 'resource'
    assert any('kaynak girişine' in e for e in validate_graph(flow))
    flow['edges'][1]['sourceHandle'] = 'other'
    assert any('success ve error' in e for e in validate_graph(flow))
