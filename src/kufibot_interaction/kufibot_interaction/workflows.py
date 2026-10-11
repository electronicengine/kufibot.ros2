"""Versioned local workflows and a ROS-independent, bounded execution engine."""
import copy
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
import unicodedata

WORKFLOW_TYPES = ('llm', 'scripted')
NO_MATCH_TEXT = 'Komutu anlayamadım, tekrar söyler misin?'
NODE_TYPES = ('start', 'agent', 'condition', 'tool', 'end', 'toolResource', 'knowledge', 'knowledgeStep', 'decision')
TOOLS = {
    'get_robot_status': {}, 'get_sensor_data': {'sensor': 'all'},
    'get_joint_positions': {}, 'set_joint_positions': {'names': [], 'angles_deg': [], 'hold_sec': 2},
    'stop_joint_motion': {}, 'list_mimics': {}, 'play_mimic': {'id': ''}, 'stop_mimic': {},
    'search_documents': {'query': '', 'collection_ids': [], 'top_k': 4},
    'play_motion': {'motion': {}},
    'move_base': {'direction': 'forward', 'speed_mps': .25, 'duration_sec': 1},
    'stop_base': {}, 'read_camera': {},
}
MOTION_TOOLS = {'set_joint_positions', 'stop_joint_motion', 'play_mimic', 'stop_mimic',
                'play_motion', 'move_base', 'stop_base'}


def uses_llm(document):
    if 'workflow_type' in document:
        return document['workflow_type'] == 'llm'
    return document.get('settings', {}).get('llm_enabled', True)


def model_kinds(document):
    kinds = ['stt', 'tts']
    if uses_llm(document):
        kinds += ['llm', 'embedding']
    elif document.get('workflow_type') == 'scripted':
        kinds += ['embedding']
    return kinds


def render_speech(template, context):
    from .workflow_values import read_value
    def replace(match):
        path = match.group(1).strip()
        if not re.fullmatch(r'(?:last_result|results|last_output|outputs)(?:\.[a-zA-Z0-9_-]+)*', path):
            raise ValueError('Geçersiz konuşma alanı: ' + path)
        value = read_value(context, path)
        if value is None or isinstance(value, (dict, list)):
            raise ValueError('Konuşma alanı bulunamadı veya basit değer değil: ' + path)
        return str(value)
    return re.sub(r'\{\{(.*?)\}\}', replace, template)


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
    settings = document.get('settings', {})
    if 'workflow_type' in document and document['workflow_type'] not in WORKFLOW_TYPES:
        errors.append('Geçersiz workflow türü')
    if not isinstance(settings, dict):
        return ['Workflow ayarları nesne olmalı']
    if type(settings.get('llm_enabled', True)) is not bool:
        errors.append('LLM kullanımı boolean olmalı')
    if not isinstance(settings.get('no_match_text', NO_MATCH_TEXT), str) or not settings.get('no_match_text', NO_MATCH_TEXT).strip():
        errors.append('Eşleşmeme metni gerekli')
    threshold = settings.get('semantic_threshold', .70)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 < threshold <= 1:
        errors.append('Anlamsal eşik 0 ile 1 arasında olmalı (0 hariç)')
    from .local_voice_settings import workflow_voice_options
    try:
        workflow_voice_options(document)
    except ValueError as error:
        errors.append(str(error))
    nodes, edges = document.get('nodes'), document.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > 100 or len(edges) > 300:
        return ['En fazla 100 node ve 300 bağlantı kullanılabilir']
    if any(not isinstance(n, dict) or n.get('type') not in NODE_TYPES or not isinstance(n.get('data'), dict) for n in nodes):
        return ['Geçersiz node']
    ids = [n.get('id') for n in nodes]
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        return ['Node kimlikleri benzersiz olmalı']
    by_id = {n['id']: n for n in nodes}
    edge_ids = [e.get('id') for e in edges if isinstance(e, dict) and e.get('id') is not None]
    if any(not isinstance(key, str) for key in edge_ids):
        errors.append('Bağlantı kimlikleri metin olmalı')
    elif len(set(edge_ids)) != len(edge_ids):
        errors.append('Bağlantı kimlikleri benzersiz olmalı')
    if sum(n['type'] == 'start' for n in nodes) != 1:
        errors.append('Tam olarak bir başlangıç gerekli')
    for edge in edges:
        if not isinstance(edge, dict) or edge.get('source') not in by_id or edge.get('target') not in by_id:
            errors.append('Bağlantı uçları bulunamadı')
            continue
        if not isinstance(edge.get('label', ''), str):
            errors.append('Geçiş tetikleme ifadeleri metin olmalı')
        source, target = by_id[edge['source']], by_id[edge['target']]
        resource = source['type'] in ('knowledge', 'toolResource')
        if resource and target['type'] != 'agent':
            errors.append('Kaynaklar yalnız ajanlara bağlanabilir')
        if resource and edge.get('targetHandle') not in (None, '', 'resource'):
            errors.append('Kaynak bağlantısı ajan kaynak girişine bağlanmalı')
        if not resource and edge.get('targetHandle') == 'resource':
            errors.append('Akış bağlantısı kaynak girişine bağlanamaz')
        if not resource and (target['type'] in ('start', 'knowledge', 'toolResource') or source['type'] == 'end'):
            errors.append('Geçersiz akış bağlantısı')
    for node in nodes:
        kind, data = node['type'], node['data']
        outgoing = [e for e in edges if isinstance(e, dict) and e.get('source') == node['id']]
        if kind == 'start' and len(outgoing) != 1:
            errors.append('Başlangıç bir node’a bağlanmalı')
        if kind in ('tool', 'toolResource') and not isinstance(data.get('arguments', {}), dict):
            errors.append('Araç parametreleri nesne olmalı')
        triggers = data.get('trigger_phrases', '')
        if not isinstance(triggers, (str, list)) or isinstance(triggers, list) and any(not isinstance(t, str) for t in triggers):
            errors.append('Tetikleme ifadeleri metin veya metin listesi olmalı')
        if kind in ('tool', 'toolResource') and data.get('tool') not in TOOLS:
            errors.append('Bilinmeyen araç: ' + str(data.get('tool')))
        if kind == 'condition':
            if data.get('operator') not in ('eq', 'contains', 'gt', 'lt', 'exists'):
                errors.append('Geçersiz koşul işleci')
            if sorted(e.get('sourceHandle', '') for e in outgoing) != ['false', 'true']:
                errors.append('Koşul true ve false çıkışları gerekli')
        if kind in ('tool', 'knowledgeStep') and sorted(e.get('sourceHandle', '') for e in outgoing) != ['error', 'success']:
            errors.append('Araç adımı success ve error çıkışları gerekli')
        if kind in ('knowledge', 'knowledgeStep') and not data.get('collection_id'):
            errors.append('Bilgi koleksiyonu seçilmeli')
        try:
            from .workflow_values import validate_output, validate_expression
            validate_output(data.get('output', {'mode':'raw'}))
            if kind in ('tool', 'toolResource'):
                validate_expression(data.get('arguments', {}))
            if kind in ('knowledge', 'knowledgeStep'):
                if type(data.get('top_k', 4)) is not int or not 1 <= data.get('top_k', 4) <= 8:
                    raise ValueError('Bilgi sonuç sayısı 1–8 olmalı')
            if kind == 'knowledgeStep':
                query = data.get('query', {'$ref':'user'})
                if not isinstance(query, (str, dict)):
                    raise ValueError('Bilgi arama girdisi metin veya alan başvurusu olmalı')
                validate_expression(query)
            if kind in ('toolResource', 'knowledge'):
                from .workflow_resources import validate_resource
                validate_resource(node)
        except (ValueError, KeyError, TypeError) as error:
            errors.append(str(error))
        if kind == 'decision':
            if document.get('workflow_type') != 'scripted':
                errors.append('Karar node’u Robot Akışı türünde kullanılabilir')
            if not settings.get('decision'):
                errors.append('Karar modeli sağlayıcısı seçilmeli')
            if not outgoing or any(not isinstance(e.get('id'), str) for e in outgoing):
                errors.append('Karar node’u kimlikli çıkış bağlantıları gerektirir')
        if kind in ('tool', 'toolResource'):
            try:
                from .workflow_robot_tools import validate_tool_arguments
                params = data.get('arguments', {})
                if not any(isinstance(v, dict) and '$ref' in v for v in params.values()) and not (kind == 'toolResource' and data.get('llm_parameters')):
                    validate_tool_arguments(data.get('tool'), params)
            except (ValueError, KeyError, TypeError) as error:
                errors.append(str(error))
        if document.get('workflow_type') == 'scripted' and kind in ('start', 'agent'):
            unconditional = [e for e in outgoing if not str(e.get('label', '')).strip()]
            if unconditional and len(outgoing) != 1:
                errors.append('Koşulsuz konuşma çıkışı tek bağlantı olmalı')
        if not uses_llm(document) and kind in ('start', 'agent', 'end'):
            content = data.get('prompt', data.get('message', ''))
            if not isinstance(content, str) or not content.strip():
                errors.append('LLM kapalıyken konuşma node içeriği gerekli')
    return errors


class WorkflowStore:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('KUFIBOT_WORKFLOW_ROOT', '~/.config/kufibot/workflows')).expanduser()

    def get(self, key):
        key = identifier(key)
        if (self.root / '.deleted' / (key + '.json')).exists():
            raise FileNotFoundError('Workflow silinmiş')
        return json.loads((self.root / (key + '.json')).read_text())

    def all(self):
        values = []
        for path in sorted(self.root.glob('*.json')):
            try:
                values.append(self.get(path.stem))
            except FileNotFoundError:
                pass  # Another process may delete a workflow while listing it.
        return values

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.store.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def check_name(self, name, key):
        if not isinstance(name, str):
            raise ValueError('Workflow adı gerekli')
        name = ' '.join(unicodedata.normalize('NFKC', name).split())
        if not name or len(name) > 120:
            raise ValueError('Workflow adı 1–120 karakter olmalı')
        def folded(value):
            value = ' '.join(unicodedata.normalize('NFKC', value).split())
            return value.replace('I', 'ı').replace('İ', 'i').casefold()
        if any(w['id'] != key and folded(w.get('name', w['id'])) == folded(name) for w in self.all()):
            raise ValueError('Bu isimde bir workflow zaten var. Farklı bir ad kullanın.')
        return name

    def save(self, value):
        if not isinstance(value, dict) or value.get('schema_version') != 1:
            raise ValueError('Workflow schema_version=1 gerekli')
        if not isinstance(value.get('nodes'), list) or not isinstance(value.get('edges'), list):
            raise ValueError('Node ve bağlantı listeleri gerekli')
        from .local_voice_settings import workflow_voice_options
        workflow_voice_options(value)
        value = copy.deepcopy(value)
        key = identifier(value.get('id') or uuid.uuid4().hex)
        with self.locked():
            return self._save(value, key)

    def _save(self, value, key):
        if (self.root / '.deleted' / (key + '.json')).exists():
            raise ValueError('Workflow silinmiş; yeni bir workflow oluşturun')
        path = self.root / (key + '.json')
        previous = self.get(key) if path.exists() else None
        if value.get('revision', 0) != (previous['revision'] if previous else 0):
            raise ValueError('Workflow değişti; yeniden yükleyin')
        if len(json.dumps(value)) > 250_000:
            raise ValueError('Workflow çok büyük')
        value['name'] = self.check_name(value.get('name', key), key)
        value.update(id=key, revision=(previous['revision'] if previous else 0) + 1)
        # Incomplete drafts are allowed; publication validates the entire graph.
        atomic_json(path, value)
        return value

    def snapshot(self, key):
        key = identifier(key)
        if (self.root / '.deleted' / (key + '.json')).exists():
            raise FileNotFoundError('Workflow silinmiş')
        path = self.root / 'published' / (key + '.json')
        value = json.loads(path.read_text())
        errors = validate_graph(value)
        if errors:
            raise ValueError('; '.join(errors))
        return value

    def publish(self, document):
        errors = validate_graph(document)
        if errors:
            raise ValueError('; '.join(errors))
        key = identifier(document['id'])
        with self.locked():
            if (self.root / '.deleted' / (key + '.json')).exists():
                raise ValueError('Workflow silinmiş; etkinleştirilemez')
            if 'name' in document:
                self.check_name(document['name'], key)
            atomic_json(self.root / 'published' / (key + '.json'), document)

    def delete(self, key, revision=None):
        key = identifier(key)
        with self.locked():
            document = self.get(key)
            if revision is not None and revision != document['revision']:
                raise ValueError('Workflow değişti; silmeden önce yeniden yükleyin')
            atomic_json(self.root / '.deleted' / (key + '.json'), {'id': key, 'revision': document['revision']})
            (self.root / 'published' / (key + '.json')).unlink(missing_ok=True)
            (self.root / (key + '.json')).unlink()
            return document

    def references(self):
        return self.all() + [json.loads(p.read_text()) for p in (self.root / 'published').glob('*.json')]


def lookup(context, path):
    from .workflow_values import read_value
    return read_value(context, path, strict=False, fresh=False)


def observation_value(context, path):
    from .workflow_values import read_value
    return read_value(context, path, strict=False)


def arguments(value, context):
    from .workflow_values import resolve
    return resolve(value, context)


class WorkflowEngine:
    def __init__(self, document, decide, call_tool, notify=lambda event: None, *, router=None, decision_provider=None, resource_selector=None):
        errors = validate_graph(document)
        if errors:
            raise ValueError('; '.join(errors))
        self.document = copy.deepcopy(document)
        self.llm_enabled = uses_llm(document)
        self.scripted = document.get('workflow_type') == 'scripted'
        self.decision_provider = decision_provider
        self.nodes = {n['id']: n for n in document['nodes']}
        self.edges = document['edges']
        self.current = next(n['id'] for n in document['nodes'] if n['type'] == 'start')
        self.decide, self.call_tool, self.notify = decide, call_tool, notify
        self.router = router
        self.resource_selector = resource_selector
        self.context = {'user': '', 'results': {}, 'outputs': {}, 'history': []}
        self.ended = False
        self.has_replied = False

    def outgoing(self, node):
        return [e for e in self.edges if e['source'] == node]

    def publish_output(self, node, result):
        from .workflow_values import project
        value = project(node['data'].get('output', {'mode':'raw'}), result, self.context)
        self.context['outputs'][node['id']] = value
        self.context['last_output'] = value
        self.context['_last_output_node'] = node['id']
        self.context['_last_output_kind'] = node['type']
        self.context['_last_output_custom'] = node['data'].get('output', {}).get('mode', 'raw') != 'raw'
        self.context.setdefault('_output_status', {})[node['id']] = result.get('status') if isinstance(result, dict) else None
        self.notify({'type':'node_output', 'node_id':node['id'], 'output':value})

    def condition(self, node):
        from .workflow_values import read_value
        data = node['data']
        op = data['operator']
        path = data.get('field', 'user')
        a = read_value(self.context, path, strict=op != 'exists' and path.startswith(('outputs.', 'last_output')))
        b = data.get('value')
        outcome = (a is not None if op == 'exists' else a == b if op == 'eq'
                   else a is not None and str(b).casefold() in str(a).casefold() if op == 'contains'
                   else type(a) in (int, float) and type(b) in (int, float) and (a > b if op == 'gt' else a < b))
        self.publish_output(node, {'matched':bool(outcome), 'value':a, 'operator':op})
        self.advance(handle=str(bool(outcome)).lower())

    def run_step(self, node):
        from .workflow_values import resolve
        data = node['data']
        name = 'search_documents' if node['type'] == 'knowledgeStep' else data['tool']
        try:
            params = ({'query':resolve(data.get('query', {'$ref':'user'}), self.context),
                       'collection_ids':[data['collection_id']], 'top_k':data.get('top_k', 4)}
                      if node['type'] == 'knowledgeStep' else arguments(data.get('arguments', {}), self.context))
        except ValueError as error:
            params = {}
            result = {'status':'error', 'error':str(error)}
        else:
            result = self.execute_tool(node, name, params, [data['collection_id']] if node['type'] == 'knowledgeStep' else data.get('collection_ids', []))
        result = self.record_tool(node, name, result)
        self.advance(handle='error' if result.get('status') in ('error', 'cancelled') else 'success')
        return result

    def execute_tool(self, node, name, params, collections):
        from .workflow_robot_tools import validate_tool_arguments
        self.notify({'type':'tool_start', 'node_id':node['id'], 'name':name, 'arguments':params})
        try:
            validate_tool_arguments(name, params)
            if name == 'search_documents':
                requested = params.get('collection_ids', collections)
                if not requested or not set(requested) <= set(collections):
                    raise ValueError('Bu koleksiyona erişim bağlı değil')
                query, top_k = params.get('query'), params.get('top_k', 4)
                if not isinstance(query, str) or not query.strip() or len(query) > 4000 or type(top_k) is not int or not 1 <= top_k <= 8:
                    raise ValueError('Arama metni 1–4000 karakter, sonuç sayısı 1–8 olmalı')
                params = {**params, 'collection_ids':requested}
            result = self.call_tool(name, params)
            if not isinstance(result, dict):
                raise ValueError('Araç sonucu nesne olmalı')
            if node['type'] == 'knowledgeStep' and result.get('status') not in ('error', 'cancelled'):
                matches = result.get('results', [])
                result = {**result, 'status':'ok', 'found':bool(matches), 'text':'\n\n'.join(item['text'] for item in matches)}
            return result
        except Exception as error:
            return {'status':'error', 'error':str(error)}

    def record_tool(self, node, name, result, *, agent_id=None):
        self.context['last_result'] = result
        self.context['results'][node['id']] = result
        if agent_id:
            self.context['results'][agent_id] = result  # Legacy agent-owned resource results.
        try:
            self.publish_output(node, result)
        except ValueError as error:
            result = {'status':'error', 'error':str(error)}
            self.context['last_result'] = result
            self.context['results'][node['id']] = result
            self.publish_output(node, result)
        self.notify({'type':'tool_result', 'node_id':node['id'], 'name':name, 'result':result})
        return result

    def advance(self, handle=None, target=None, edge_id=None):
        choices = self.outgoing(self.current)
        if edge_id is not None:
            choices = [e for e in choices if e.get('id') == edge_id]
        if target is not None:
            choices = [e for e in choices if e['target'] == target]
        if handle is not None:
            choices = [e for e in choices if e.get('sourceHandle') == handle]
        if len(choices) != 1:
            raise ValueError('Geçiş tek bir izinli bağlantıyı seçmeli')
        previous = self.current
        self.current = choices[0]['target']
        self.has_replied = False
        self.notify({'type': 'transition', 'from_node': previous, 'node_id': self.current})

    def turn(self, text):
        if self.ended:
            raise ValueError('Görüşme sona erdi')
        if self.scripted:
            return self._scripted_turn(text)
        user_text = text
        self.context['user'] = text
        # Published node outputs and tool results belong to the whole session.
        calls, speech, sources = 0, [], []
        self.context['_resource_calls'] = []
        routed = False
        for _ in range(16):
            node = self.nodes[self.current]
            data, kind = node['data'], node['type']
            self.notify({'type': 'node', 'node_id': self.current})
            if kind == 'condition':
                self.condition(node)
                continue
            if kind in ('tool', 'knowledgeStep'):
                if calls >= 4:
                    raise ValueError('Tur başına dört araç çağrısı sınırı aşıldı')
                calls += 1
                result = self.run_step(node)
                sources.extend(result.get('results', []))
                continue
            attached = [self.nodes[e['source']] for e in self.edges if e['target'] == self.current
                        and self.nodes[e['source']]['type'] in ('toolResource', 'knowledge')]
            allowed = [n['data']['tool'] for n in attached if n['type'] == 'toolResource']
            collections = [n['data']['collection_id'] for n in attached if n['type'] == 'knowledge']
            if collections:
                allowed.append('search_documents')
            collections += [key for n in attached if n['type'] == 'toolResource' and n['data']['tool'] == 'search_documents'
                            for key in n['data'].get('collection_ids', [])]
            if kind in ('agent', 'start', 'end'):
                # One semantic action per user utterance. After a transition,
                # the target node replies with its own prompt; it cannot consume
                # the same utterance again and skip across multiple agents.
                decision = None
                if (kind == 'agent' or kind == 'start' and self.has_replied) and not routed and calls == 0 and self.router:
                    routed = True
                    decision = self.router(node, {**self.context, '_transition_allowed': self.has_replied})
                    # Enforce the gate here too, independent of the router implementation.
                    if decision and decision.get('action') == 'transition' and not self.has_replied:
                        decision = None
                selected_resource = None
                if decision is None and self.llm_enabled and kind == 'agent' and self.resource_selector and attached:
                    from .workflow_resources import resource_contract, resource_arguments, tool_name
                    choice = self.resource_selector(node, copy.deepcopy(self.context), [resource_contract(n) for n in attached])
                    if choice is not None:
                        selected_resource = next((n for n in attached if n['id'] == choice.get('resource_id')), None)
                        if selected_resource is None:
                            raise ValueError('Ajan izinli olmayan bir kaynak seçti')
                        decision = {'action':'tool', 'tool':tool_name(selected_resource),
                                    'arguments':resource_arguments(selected_resource, choice.get('arguments', {}), self.context)}
                if decision is not None:
                    if decision.get('action') == 'transition':
                        if not isinstance(decision.get('target'), str):
                            raise ValueError('Geçiş hedefi gerekli')
                        self.advance(target=decision.get('target'), edge_id=decision.get('edge_id'))
                        continue
                    if decision.get('action') != 'tool' or decision.get('tool') not in allowed:
                        raise ValueError('Ajan izinli olmayan bir araç/işlem seçti')
                    name, params = decision['tool'], decision.get('arguments', {})
                else:
                    if self.llm_enabled:
                        decision = self.decide(node, {**self.context, '_require_llm': not self.has_replied}, [], collections, [])
                        if decision.get('action') != 'reply':
                            raise ValueError('Dil modeli yalnız kullanıcı yanıtı üretebilir')
                        if not isinstance(decision.get('text'), str) or not decision['text'].strip():
                            raise ValueError('Model boş workflow yanıtı üretti')
                        text = decision['text']
                    else:
                        text = render_speech(data.get('prompt', data.get('message', '')), self.context)
                        if not isinstance(text, str) or not text.strip():
                            raise ValueError('LLM kapalıyken konuşma node içeriği gerekli')
                    speech.append(text)
                    self.publish_output(node, {'text':text})
                    self.has_replied = True
                    if kind == 'end':
                        self.ended = True
                    elif kind == 'start' and not str(self.outgoing(self.current)[0].get('label', '')).strip():
                        # An unlabelled entry edge is unconditional, but even entry
                        # must finish its own reply before advancing. Labelled entry
                        # edges are evaluated on a later user turn by the router.
                        self.advance()
                        self.notify({'type': 'node', 'node_id': self.current})
                        if self.document.get('workflow_type') == 'llm' and self.nodes[self.current]['type'] in ('tool', 'knowledgeStep', 'condition'):
                            continue
                    break
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
            resource_node = selected_resource or node
            result = self.execute_tool(resource_node, name, params, collections)
            resource = selected_resource or next((n for n in attached if (n['type'] == 'knowledge' and name == 'search_documents') or n['type'] == 'toolResource' and n['data']['tool'] == name), node)
            result = self.record_tool(resource, name, result, agent_id=self.current)
            self.context['_resource_calls'].append({'resource_id':resource['id'], 'arguments':params, 'result':result})
            if name == 'search_documents':
                sources.extend(result.get('results', []))
        else:
            raise ValueError('16 node geçişi sınırı aşıldı')
        answer = '\n'.join(speech)
        self.context['history'] = (self.context['history'] + [{'user': user_text, 'assistant': answer}])[-2:]
        return {'text': answer, 'sources': sources, 'ended': self.ended}

    def _scripted_turn(self, user_text):
        self.context['user'] = user_text
        speech, sources, calls, routed = [], [], 0, False
        settings = self.document.get('settings', {})
        for _ in range(16):
            node = self.nodes[self.current]
            kind, data = node['type'], node['data']
            self.notify({'type': 'node', 'node_id': self.current})
            if kind == 'condition':
                self.condition(node)
                continue
            if kind in ('tool', 'knowledgeStep'):
                if calls >= 4:
                    raise ValueError('Tur başına dört araç çağrısı sınırı aşıldı')
                calls += 1
                result = self.run_step(node)
                sources.extend(result.get('results', []))
                continue
            if kind == 'decision':
                if self.decision_provider is None:
                    raise ValueError('Karar modeli sağlayıcısı kullanılamıyor')
                choices = self.outgoing(self.current)
                result = self.decision_provider(node, copy.deepcopy(self.context), copy.deepcopy(choices))
                edge = next((e for e in choices if e['id'] == result.get('edge_id')), None)
                if edge is None:
                    raise ValueError('Karar modeli izinli bir çıkış seçmeli')
                self.publish_output(node, {'edge_id':edge['id']})
                self.notify({'type': 'decision', 'node_id': self.current, 'edge_id': edge['id']})
                self.advance(edge_id=edge['id'])
                continue
            if kind in ('start', 'agent', 'end'):
                if not self.has_replied:
                    spoken = render_speech(data.get('prompt', data.get('message', '')), self.context)
                    speech.append(spoken)
                    self.publish_output(node, {'text':spoken})
                    self.has_replied = True
                    if kind == 'end':
                        self.ended = True
                        break
                    outgoing = self.outgoing(self.current)
                    if len(outgoing) == 1 and not str(outgoing[0].get('label', '')).strip():
                        self.advance()
                        continue
                    break
                if routed or not user_text.strip():
                    break
                routed = True
                if self.router is None:
                    raise ValueError('Embedding yönlendiricisi kullanılamıyor')
                choice = self.router(node, {**self.context, '_transition_allowed': True})
                if choice is None:
                    speech.append(settings.get('no_match_text', NO_MATCH_TEXT))
                    break
                if choice.get('action') == 'transition':
                    self.advance(target=choice.get('target'), edge_id=choice.get('edge_id'))
                    continue
                attached = [self.nodes[e['source']] for e in self.edges if e['target'] == self.current
                            and self.nodes[e['source']]['type'] in ('toolResource', 'knowledge')]
                allowed = {n['data']['tool'] if n['type'] == 'toolResource' else 'search_documents' for n in attached}
                if choice.get('action') != 'tool' or choice.get('tool') not in allowed:
                    raise ValueError('İzinli olmayan araç seçildi')
                name, params = choice['tool'], choice.get('arguments', {})
                collections = [n['data']['collection_id'] for n in attached if n['type'] == 'knowledge']
                collections += [k for n in attached if n['type'] == 'toolResource' for k in n['data'].get('collection_ids', [])]
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
            resource = next((n for n in attached if n['id'] == choice.get('resource_id')), None) or next((n for n in attached if n['type'] == 'knowledge' and name == 'search_documents' or n['type'] == 'toolResource' and n['data']['tool'] == name), node)
            result = self.execute_tool(resource, name, params, collections)
            self.record_tool(resource, name, result, agent_id=self.current)
            if name == 'search_documents':
                sources.extend(result.get('results', []))
            # Resource calls keep entry speech from repeating.
            break
        else:
            raise ValueError('16 node geçişi sınırı aşıldı')
        answer = '\n'.join(speech)
        self.context['history'] = (self.context['history'] + [{'user': user_text, 'assistant': answer}])[-2:]
        return {'text': answer, 'sources': sources, 'ended': self.ended}
