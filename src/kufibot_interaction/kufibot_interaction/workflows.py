"""Versioned local workflows and a ROS-independent, bounded execution engine."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

NODE_TYPES = ('start', 'agent', 'condition', 'tool', 'end', 'toolResource', 'knowledge')
TOOLS = {
    'get_robot_status': {}, 'get_sensor_data': {'sensor': 'all'},
    'get_joint_positions': {}, 'set_joint_positions': {'names': [], 'angles_deg': [], 'hold_sec': 2},
    'stop_joint_motion': {}, 'list_mimics': {}, 'play_mimic': {'id': ''}, 'stop_mimic': {},
    'search_documents': {'query': '', 'collection_ids': [], 'top_k': 4},
}
MOTION_TOOLS = {'set_joint_positions', 'stop_joint_motion', 'play_mimic', 'stop_mimic'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value):
        raise ValueError('Geçersiz kimlik')
    return value


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_graph(document):
    errors = []
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        return ['Workflow schema_version=1 gerekli']
    nodes, edges = document.get('nodes'), document.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 100 or len(edges) > 300:
        return ['En fazla 100 node ve 300 bağlantı kullanılabilir']
    if any(not isinstance(n, dict) or n.get('type') not in NODE_TYPES or not isinstance(n.get('data'), dict) for n in nodes):
        return ['Geçersiz node']
    ids = [n.get('id') for n in nodes]
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        return ['Node kimlikleri benzersiz olmalı']
    by_id = {n['id']: n for n in nodes}
    if sum(n['type'] == 'start' for n in nodes) != 1:
        errors.append('Tam olarak bir başlangıç gerekli')
    for edge in edges:
        if not isinstance(edge, dict) or edge.get('source') not in by_id or edge.get('target') not in by_id:
            errors.append('Bağlantı uçları bulunamadı')
            continue
        source, target = by_id[edge['source']], by_id[edge['target']]
        resource = source['type'] in ('knowledge', 'toolResource')
        if resource and target['type'] != 'agent':
            errors.append('Kaynaklar yalnız ajanlara bağlanabilir')
        if not resource and (target['type'] in ('start', 'knowledge', 'toolResource') or source['type'] == 'end'):
            errors.append('Geçersiz akış bağlantısı')
    for node in nodes:
        kind, data = node['type'], node['data']
        outgoing = [e for e in edges if isinstance(e, dict) and e.get('source') == node['id']]
        if kind == 'start' and len(outgoing) != 1:
            errors.append('Başlangıç bir node’a bağlanmalı')
        if kind in ('tool', 'toolResource') and data.get('tool') not in TOOLS:
            errors.append('Bilinmeyen araç: ' + str(data.get('tool')))
        if kind == 'condition':
            if data.get('operator') not in ('eq', 'contains', 'gt', 'lt', 'exists'):
                errors.append('Geçersiz koşul işleci')
            if sorted(e.get('sourceHandle', '') for e in outgoing) != ['false', 'true']:
                errors.append('Koşul true ve false çıkışları gerekli')
        if kind == 'tool' and sorted(e.get('sourceHandle', '') for e in outgoing) != ['error', 'success']:
            errors.append('Araç adımı success ve error çıkışları gerekli')
        if kind == 'knowledge' and not data.get('collection_id'):
            errors.append('Bilgi koleksiyonu seçilmeli')
    return errors


class WorkflowStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('KUFIBOT_WORKFLOW_ROOT', '~/.config/kufibot/workflows')).expanduser()

    def get(self, key):
        return json.loads((self.root / (identifier(key) + '.json')).read_text())

    def all(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*.json'))]

    def save(self, value):
        if not isinstance(value, dict) or value.get('schema_version') != 1:
            raise ValueError('Workflow schema_version=1 gerekli')
        if not isinstance(value.get('nodes'), list) or not isinstance(value.get('edges'), list):
            raise ValueError('Node ve bağlantı listeleri gerekli')
        value = copy.deepcopy(value)
        key = identifier(value.get('id') or uuid.uuid4().hex)
        path = self.root / (key + '.json')
        previous = self.get(key) if path.exists() else None
        if value.get('revision', 0) != (previous['revision'] if previous else 0):
            raise ValueError('Workflow değişti; yeniden yükleyin')
        if len(json.dumps(value)) > 250_000:
            raise ValueError('Workflow çok büyük')
        value.update(id=key, revision=(previous['revision'] if previous else 0) + 1)
        # Incomplete drafts are allowed; publication validates the entire graph.
        atomic_json(path, value)
        return value

    def snapshot(self, key):
        path = self.root / 'published' / (identifier(key) + '.json')
        value = json.loads(path.read_text())
        errors = validate_graph(value)
        if errors:
            raise ValueError('; '.join(errors))
        return value

    def publish(self, document):
        errors = validate_graph(document)
        if errors:
            raise ValueError('; '.join(errors))
        atomic_json(self.root / 'published' / (identifier(document['id']) + '.json'), document)

    def references(self):
        return self.all() + [json.loads(p.read_text()) for p in (self.root / 'published').glob('*.json')]


def lookup(context, path):
    value = context
    for part in str(path).split('.'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def arguments(value, context):
    if isinstance(value, dict):
        if set(value) == {'$ref'}:
            return lookup(context, value['$ref'])
        return {k: arguments(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [arguments(v, context) for v in value]
    return value


class WorkflowEngine:
    def __init__(self, document, decide, call_tool, notify=lambda event: None):
        errors = validate_graph(document)
        if errors:
            raise ValueError('; '.join(errors))
        self.document = copy.deepcopy(document)
        self.nodes = {n['id']: n for n in document['nodes']}
        self.edges = document['edges']
        self.current = next(n['id'] for n in document['nodes'] if n['type'] == 'start')
        self.decide, self.call_tool, self.notify = decide, call_tool, notify
        self.context = {'user': '', 'results': {}, 'history': []}
        self.ended = False

    def outgoing(self, node):
        return [e for e in self.edges if e['source'] == node]

    def advance(self, handle=None, target=None):
        choices = self.outgoing(self.current)
        if target is not None:
            choices = [e for e in choices if e['target'] == target]
        if handle is not None:
            choices = [e for e in choices if e.get('sourceHandle') == handle]
        if len(choices) != 1:
            raise ValueError('Geçiş tek bir izinli bağlantıyı seçmeli')
        previous = self.current
        self.current = choices[0]['target']
        self.notify({'type': 'transition', 'from_node': previous, 'node_id': self.current})

    def turn(self, text):
        if self.ended:
            raise ValueError('Görüşme sona erdi')
        self.context['user'] = text
        self.context['results'] = {}
        self.context.pop('last_result', None)
        calls, speech, sources = 0, [], []
        for _ in range(16):
            node = self.nodes[self.current]
            data, kind = node['data'], node['type']
            self.notify({'type': 'node', 'node_id': self.current})
            if kind in ('start', 'end') and not data.get('prompt', data.get('message', '')):
                if kind == 'end':
                    self.ended = True
                    break
                self.advance()
                continue
            if kind == 'condition':
                a, b, op = lookup(self.context, data.get('field')), data.get('value'), data['operator']
                outcome = (a is not None if op == 'exists' else a == b if op == 'eq'
                           else str(b).casefold() in str(a).casefold() if op == 'contains'
                           else isinstance(a, (int, float)) and isinstance(b, (int, float)) and
                           (a > b if op == 'gt' else a < b))
                self.advance(handle=str(bool(outcome)).lower())
                continue
            attached = [self.nodes[e['source']] for e in self.edges if e['target'] == self.current
                        and self.nodes[e['source']]['type'] in ('toolResource', 'knowledge')]
            allowed = [n['data']['tool'] for n in attached if n['type'] == 'toolResource']
            collections = [n['data']['collection_id'] for n in attached if n['type'] == 'knowledge']
            if collections:
                allowed.append('search_documents')
            if kind in ('agent', 'start', 'end'):
                # Retrieved prose may inform the answer, but must not trigger
                # additional model-selected actions in this turn.
                document_answer = bool(sources)
                # Entry has one validated outgoing edge. Speak its greeting
                # first, then advance without an extra routing inference.
                reply_only = document_answer or kind in ('start', 'end')
                decision = self.decide(node, self.context, [] if reply_only else allowed,
                                       collections, [] if reply_only else self.outgoing(self.current))
                action = decision.get('action')
                if document_answer and action != 'reply':
                    raise ValueError('Belge sonucundan sonra yalnız kullanıcı yanıtı üretilebilir')
                if action == 'reply':
                    if not isinstance(decision.get('text'), str) or not decision['text'].strip():
                        raise ValueError('Model boş workflow yanıtı üretti')
                    speech.append(decision['text'])
                    if kind == 'end':
                        self.ended = True
                    elif kind == 'start':
                        self.advance()
                        self.notify({'type': 'node', 'node_id': self.current})
                    break
                if kind == 'end':
                    raise ValueError('Bitiş node’u yalnız kullanıcı yanıtı üretebilir')
                if action == 'transition':
                    previous = self.current
                    self.advance(target=decision.get('target'))
                    self.notify({'type': 'tool_start', 'node_id': previous,
                                 'name': 'transition_node', 'arguments': {'target': self.current}})
                    self.notify({'type': 'tool_result', 'node_id': previous,
                                 'name': 'transition_node', 'result': {'status': 'ok', 'node_id': self.current}})
                    continue
                if action != 'tool' or decision.get('tool') not in allowed:
                    raise ValueError('Ajan izinli olmayan bir araç/işlem seçti')
                name, params = decision['tool'], decision.get('arguments', {})
            else:
                name, params = data['tool'], arguments(data.get('arguments', {}), self.context)
                collections = data.get('collection_ids', [])
            if calls >= 4:
                raise ValueError('Tur başına dört araç çağrısı sınırı aşıldı')
            if not isinstance(params, dict):
                raise ValueError('Araç parametreleri nesne olmalı')
            if name == 'search_documents':
                requested = params.get('collection_ids', collections)
                if not requested or not set(requested) <= set(collections):
                    raise ValueError('Bu koleksiyona erişim bağlı değil')
                params = {**params, 'collection_ids': requested}
            calls += 1
            self.notify({'type': 'tool_start', 'node_id': self.current, 'name': name, 'arguments': params})
            try:
                result = self.call_tool(name, params)
            except Exception as error:
                result = {'status': 'error', 'error': str(error)}
            self.context['results'][self.current] = result
            self.context['last_result'] = result
            self.notify({'type': 'tool_result', 'node_id': self.current, 'name': name, 'result': result})
            if name == 'search_documents':
                sources.extend(result.get('results', []))
            if kind == 'tool':
                self.advance(handle='error' if result.get('status') == 'error' else 'success')
        else:
            raise ValueError('16 node geçişi sınırı aşıldı')
        answer = '\n'.join(speech)
        self.context['history'] = (self.context['history'] + [{'user': text, 'assistant': answer}])[-2:]
        return {'text': answer, 'sources': sources, 'ended': self.ended}
