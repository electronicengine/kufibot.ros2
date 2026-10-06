"""Isolated text-only workflow smoke runner; tools are dispatched by the parent."""
import json
import sys
import uuid

from .workflows import WorkflowEngine
from .workflow_inference import ToolChannel


def main():
    model = router = None
    def emit(kind, **fields):
        print(json.dumps({'type': kind, **fields}, ensure_ascii=False), flush=True)
    try:
        config = json.loads(sys.stdin.readline())
        channel = ToolChannel(emit, uuid.uuid4().hex)
        workflow = config['workflow']
        decider = None
        if workflow.get('settings', {}).get('llm_enabled', True):
            from llama_cpp import Llama
            from .workflow_inference import make_decider
            from .workflow_routing import SemanticRouter
            model = Llama(model_path=config['llm'], n_ctx=2048, n_threads=3, verbose=False)
            router = SemanticRouter(workflow, config.get('embedding'), notify=lambda event: emit('trace', event=event))
            decider = make_decider(model)
        engine = WorkflowEngine(workflow, decider, channel.call,
                                lambda event: emit('trace', event=event), router=router)
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
        try:
            if model:
                model.close()
        finally:
            if router:
                router.close()


if __name__ == '__main__':
    main()
