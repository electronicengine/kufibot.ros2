"""Isolated text-only workflow smoke runner; tools are dispatched by the parent."""
import json
import sys
import uuid

from .workflows import WorkflowEngine
from .workflow_inference import make_decider, ToolChannel


def main():
    model = None
    def emit(kind, **fields):
        print(json.dumps({'type': kind, **fields}, ensure_ascii=False), flush=True)
    try:
        config = json.loads(sys.stdin.readline())
        from llama_cpp import Llama
        model = Llama(model_path=config['llm'], n_ctx=2048, n_threads=3, verbose=False)
        channel = ToolChannel(emit, uuid.uuid4().hex)
        workflow = config['workflow']
        settings = workflow['settings']
        engine = WorkflowEngine(workflow, make_decider(model, settings.get('system_prompt', ''),
            settings.get('language', 'tr')), channel.call,
            lambda event: emit('trace', event=event))
        text = config['text']
        while True:
            channel.turn_id += 1
            result = engine.turn(text)
            emit('answer', **result)
            if result['ended']:
                break
            line = sys.stdin.readline()
            if not line:
                break
            text = json.loads(line)['text']
    except Exception as exc:
        emit('error', message=str(exc))
    finally:
        if model:
            model.close()


if __name__ == '__main__':
    main()
