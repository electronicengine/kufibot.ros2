"""Local semantic action selection; the chat model never selects actions."""
from functools import lru_cache
import math

import numpy as np

from .workflows import arguments

DEFAULT_THRESHOLD = 0.70


def phrases(value):
    """One user-authored example per line (also accept a list in saved graphs)."""
    values = value.splitlines() if isinstance(value, str) else value or []
    return list(dict.fromkeys(item.strip() for item in values if isinstance(item, str) and item.strip()))


class SemanticRouter:
    def __init__(self, document, model_path, *, factory=None, notify=lambda event: None,
                 metric=lambda *args, **kwargs: None):
        self.threshold = document.get('settings', {}).get('semantic_threshold', DEFAULT_THRESHOLD)
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float)) or not math.isfinite(self.threshold) or not 0 < self.threshold <= 1:
            raise ValueError('Anlamsal eşik 0 ile 1 arasında olmalı (0 hariç)')
        self.model_path, self.factory = model_path, factory
        self.notify, self.metric = notify, metric
        self.embedder = None
        self.candidates = {}
        nodes = {n['id']: n for n in document['nodes']}
        for node in document['nodes']:
            if node['type'] not in ('start', 'agent'):
                continue
            choices = []
            for edge in document['edges']:
                if edge['source'] == node['id']:
                    examples = phrases(edge.get('label'))
                    if examples:
                        choices.append((examples, {'action': 'transition', 'target': edge['target']}))
                if edge['target'] != node['id']:
                    continue
                resource = nodes[edge['source']]
                data = resource['data']
                if resource['type'] not in ('toolResource', 'knowledge'):
                    continue
                examples = phrases(data.get('trigger_phrases'))
                if not examples:
                    continue
                if resource['type'] == 'knowledge':
                    decision = {'action': 'tool', 'tool': 'search_documents', 'arguments': {
                        'query': {'$ref': 'user'}, 'collection_ids': [data['collection_id']], 'top_k': 4}}
                else:
                    decision = {'action': 'tool', 'tool': data['tool'], 'arguments': data.get('arguments', {})}
                    if data['tool'] == 'search_documents':
                        decision['arguments'] = {**decision['arguments'], 'collection_ids': data.get('collection_ids', [])}
                choices.append((examples, decision))
            self.candidates[node['id']] = choices

    @lru_cache(maxsize=2048)
    def vector(self, text):
        if self.embedder is None:
            if not self.model_path:
                raise ValueError('Anlamsal yönlendirme için embedding modeli seçilmeli')
            factory = self.factory
            if factory is None:
                from .knowledge import Embedder
                factory = Embedder
            self.embedder = factory(self.model_path)
        value = np.asarray(self.embedder.vector(text), dtype=np.float64)
        norm = np.linalg.norm(value)
        if value.ndim != 1 or not np.isfinite(value).all() or not np.isfinite(norm) or norm <= 0:
            raise ValueError('Model geçerli embedding üretmiyor')
        return value / norm

    def __call__(self, node, context):
        text = str(context.get('user', '')).strip()
        choices = [(examples, decision) for examples, decision in self.candidates.get(node['id'], [])
                   if decision['action'] != 'transition' or context.get('_transition_allowed', True)]
        if not text or not choices:
            return None
        self.metric('workflow_route_start')
        try:
            query = self.vector(text)
            ranked = []
            for examples, decision in choices:
                score, example = max((float(np.dot(query, self.vector(example))), example) for example in examples)
                ranked.append((max(-1.0, min(1.0, score)), example, decision))
            ranked.sort(key=lambda item: item[0], reverse=True)
            score, example, decision = ranked[0]
            ambiguous = len(ranked) > 1 and abs(score - ranked[1][0]) < 1e-6
            matched = score >= self.threshold and not ambiguous
            self.notify({'type': 'semantic_match', 'node_id': node['id'], 'score': score,
                         'threshold': self.threshold, 'matched': matched, 'ambiguous': ambiguous,
                         'trigger': example, 'action': decision['action'],
                         'target': decision.get('target'), 'tool': decision.get('tool')})
            if not matched:
                return None
            result = dict(decision)
            if result['action'] == 'tool':
                result['arguments'] = arguments(result['arguments'], context)
            return result
        finally:
            self.metric('workflow_route_end')

    def close(self):
        self.vector.cache_clear()
        if self.embedder is not None:
            self.embedder.close()
            self.embedder = None
