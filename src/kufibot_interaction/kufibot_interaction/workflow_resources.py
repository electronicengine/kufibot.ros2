"""Parameter contracts for attached, model-selected workflow resources."""
import copy
import math

from .workflow_values import resolve

PARAMETERS = {
    'get_robot_status': {}, 'get_sensor_data': {'sensor': {'type':'string', 'enum':['all','battery','distance','compass']}},
    'get_joint_positions': {},
    'set_joint_positions': {'names': {'type':'array', 'items':{'type':'string'}},
                            'angles_deg': {'type':'array', 'items':{'type':'number'}},
                            'hold_sec': {'type':'number', 'minimum':.1, 'maximum':10}},
    'stop_joint_motion': {}, 'list_mimics': {}, 'play_mimic': {'id':{'type':'string'}}, 'stop_mimic': {},
    'search_documents': {'query': {'type':'string', 'minLength':1, 'maxLength':4000},
                         'top_k': {'type':'integer', 'minimum':1, 'maximum':8}},
    'play_motion': {'motion': {'type':'object'}},
    'move_base': {'direction': {'type':'string', 'enum':['forward','backward']},
                  'speed_mps': {'type':'number', 'exclusiveMinimum':0},
                  'duration_sec': {'type':'number', 'exclusiveMinimum':0, 'maximum':5}},
    'stop_base': {}, 'read_camera': {},
}
OUTPUT_FIELDS = {
    'start':['text'], 'agent':['text'], 'end':['text'], 'condition':['matched','value','operator'],
    'decision':['edge_id'], 'knowledgeStep':['status','found','text','results.0.text','results.0.score','results.0.filename','results.0.location'],
    'knowledge':['status','results.0.text','results.0.score','results.0.filename'],
    'tool': ['status'], 'toolResource':['status'],
}
TOOL_OUTPUT_FIELDS = {
    'get_robot_status':['mode','sensors.voltage','sensors.distance'],
    'get_sensor_data':['battery.voltage','range_m.value','compass_deg.value','sensors.voltage','sensors.distance'],
    'get_joint_positions':['positions.neck','positions.rightArm'],
    'read_camera':['status','perception.faces.status','perception.faces.value','perception.objects.value'],
    'search_documents':['status','results.0.text','results.0.filename','results.0.location','results.0.score'],
    'set_joint_positions':['status','applied','clamped'], 'play_motion':['status','accepted','motion_id'],
    'move_base':['status','duration_sec'],
}


def tool_name(node):
    return 'search_documents' if node['type'] in ('knowledge', 'knowledgeStep') else node['data']['tool']


def editable_fields(node):
    return node['data'].get('llm_parameters', ['query'] if node['type'] == 'knowledge' else [])


def validate_resource(node):
    fields = editable_fields(node)
    allowed = PARAMETERS.get(tool_name(node), {})
    if not isinstance(fields, list) or any(not isinstance(key, str) or key not in allowed for key in fields) or len(set(fields)) != len(fields):
        raise ValueError('Geçersiz LLM parametre izni')


def configured_arguments(node, context):
    data = node['data']
    if node['type'] == 'knowledge':
        return {'query':context.get('user', ''), 'collection_ids':[data['collection_id']], 'top_k':data.get('top_k', 4)}
    value = resolve(data.get('arguments', {}), context)
    if tool_name(node) == 'search_documents':
        value['collection_ids'] = data.get('collection_ids', [])
    return value


def resource_arguments(node, supplied, context):
    validate_resource(node)
    if not isinstance(supplied, dict) or not set(supplied) <= set(editable_fields(node)):
        raise ValueError('LLM sabit veya izinsiz araç parametresini değiştiremez')
    value = configured_arguments(node, context)
    # Treat model arguments as data, never as workflow references.
    def has_reference(item):
        return isinstance(item, dict) and ('$ref' in item or any(has_reference(v) for v in item.values())) or isinstance(item, list) and any(has_reference(v) for v in item)
    if has_reference(supplied):
        raise ValueError('Model parametresi alan başvurusu içeremez')
    for key, item in supplied.items():
        validate_parameter(item, PARAMETERS[tool_name(node)][key])
    value.update(copy.deepcopy(supplied))
    return value


def validate_parameter(value, schema):
    kind = schema['type']
    valid = (isinstance(value, str) if kind == 'string' else type(value) is int if kind == 'integer'
             else type(value) in (int, float) and math.isfinite(value) if kind == 'number'
             else isinstance(value, list) if kind == 'array' else isinstance(value, dict))
    if not valid or 'enum' in schema and value not in schema['enum']:
        raise ValueError('Model araç parametresi şemaya uymuyor')
    if kind in ('number', 'integer'):
        if not math.isfinite(value) or 'minimum' in schema and value < schema['minimum'] or 'maximum' in schema and value > schema['maximum'] or 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
            raise ValueError('Model araç parametresi sınır dışında')
    if kind == 'string' and not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 10000):
        raise ValueError('Model araç metni sınır dışında')
    if kind == 'array':
        for item in value:
            validate_parameter(item, schema['items'])


def resource_contract(node):
    validate_resource(node)
    name = tool_name(node)
    fields = editable_fields(node)
    return {'id':node['id'], 'name':node['data'].get('name', name), 'tool':name,
            'description':node['data'].get('description', ''),
            'examples':node['data'].get('trigger_phrases', ''),
            'fixed_arguments':({'collection_ids':[node['data']['collection_id']], 'top_k':node['data'].get('top_k',4)} if node['type'] == 'knowledge' else node['data'].get('arguments', {})),
            'parameters':{key:PARAMETERS[name][key] for key in fields}}
