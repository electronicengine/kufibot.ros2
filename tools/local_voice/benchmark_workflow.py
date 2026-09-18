#!/usr/bin/env python3
"""Offline workflow/embedding check; never touches microphones or robot actuators."""
import argparse
import json
from pathlib import Path
import resource
import tempfile
import time

from kufibot_interaction.knowledge import KnowledgeStore
from kufibot_interaction.workflows import WorkflowEngine
from kufibot_interaction.workflow_inference import make_decider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm', default='/usr/local/ai.models/llamaModel/ufakzeka-1-q8_0.gguf')
    parser.add_argument('--embedding', default='embedding:mxbaiV1')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kufibot-workflow-check-') as directory:
        root = Path(directory)
        store = KnowledgeStore(root / 'knowledge')
        store.queue = lambda key: None
        key = store.create('Türkçe test kılavuzu', args.embedding)['id']
        document = root / 'manual.json'
        document.write_text(json.dumps({'pil': 'Robotun pili 12 volttur. Şarj süresi iki saattir.',
                                       'renk': 'Robotun gövdesi mavidir.',
                                       'temizlik': 'Robot kuru bezle temizlenir.'}, ensure_ascii=False))
        store.add(key, document, 'manual.json')
        started = time.monotonic()
        store.index(key)
        print(json.dumps({'event': 'index', 'seconds': time.monotonic()-started,
                          'state': store.collection(key)['state'], 'error': store.collection(key)['error']}), flush=True)
        for query in ('Pilin voltajı kaç?', 'Robot nasıl temizlenir?', 'Gövde ne renk?'):
            started = time.monotonic()
            result = store.search(query, [key], 1)
            print(json.dumps({'event': 'search', 'query': query, 'seconds': time.monotonic()-started,
                              'result': result}, ensure_ascii=False), flush=True)
        from llama_cpp import Llama
        model = Llama(model_path=args.llm, n_ctx=2048, n_threads=3, verbose=False)
        try:
            decide = make_decider(model)
            for question, tool in [('Pilin durumu nedir?', 'get_sensor_data'),
                                   ('Kılavuza göre robot nasıl temizlenir?', 'search_documents')]:
                started = time.monotonic()
                result = decide({'data': {'prompt': 'Soruyu yanıtlamak için bağlı aracı kullan.'}},
                                {'user': question, 'results': {}}, [tool], [key] if tool == 'search_documents' else [], [])
                print(json.dumps({'event': 'decision', 'question': question,
                    'seconds': time.monotonic()-started, 'expected_tool': tool,
                    'correct': result.get('action') == 'tool' and result.get('tool') == tool,
                    'decision': result}, ensure_ascii=False), flush=True)
            workflow = {'schema_version': 1, 'nodes': [
                {'id': 's', 'type': 'start', 'data': {}},
                {'id': 'search', 'type': 'tool', 'data': {'tool': 'search_documents',
                    'collection_ids': [key], 'arguments': {'query': {'$ref': 'user'}, 'top_k': 1}}},
                {'id': 'a', 'type': 'agent', 'data': {'prompt': 'Araç sonucundaki belgeye göre Türkçe kısa yanıt ver.'}},
                {'id': 'error', 'type': 'end', 'data': {'message': 'Belge okunamadı'}}],
                'edges': [{'source': 's', 'target': 'search'},
                          {'source': 'search', 'target': 'a', 'sourceHandle': 'success'},
                          {'source': 'search', 'target': 'error', 'sourceHandle': 'error'}]}
            engine = WorkflowEngine(workflow, decide, lambda name, params: store.search(**params))
            started = time.monotonic()
            print(json.dumps({'event': 'fixed_retrieval', 'result': engine.turn('Robot nasıl temizlenir?'),
                              'seconds': time.monotonic()-started}, ensure_ascii=False), flush=True)
        finally:
            model.close()
        print(json.dumps({'event': 'resources', 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)


if __name__ == '__main__':
    main()
