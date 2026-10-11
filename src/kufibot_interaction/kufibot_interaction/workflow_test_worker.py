"""Isolated text-only workflow smoke runner; tools are dispatched by the parent."""
import json
import sys
import uuid

from .workflows import WorkflowEngine, uses_llm
from .workflow_inference import ToolChannel


def main():
    model = router = decision_provider = None
    def emit(kind, **fields):
        print(json.dumps({'type': kind, **fields}, ensure_ascii=False), flush=True)
    try:
        config = json.loads(sys.stdin.readline())
        channel = ToolChannel(emit, uuid.uuid4().hex)
        workflow = config['workflow']
        decider = resource_selector = None
        if uses_llm(workflow) or workflow.get('workflow_type') == 'scripted':
            from .workflow_routing import SemanticRouter
            router = SemanticRouter(workflow, config.get('embedding'), notify=lambda event: emit('trace', event=event), include_resources=not uses_llm(workflow))
        if config.get('decision_model'):
            from .workflow_decision import DecisionProvider
            decision_provider = DecisionProvider(config['decision_model'])
        if uses_llm(workflow):
            from llama_cpp import Llama
            from .workflow_inference import make_decider, make_resource_selector
            model = Llama(model_path=config['llm'], n_ctx=2048, n_threads=3, verbose=False)
            decider = make_decider(model)
            resource_selector = make_resource_selector(model)
        engine = WorkflowEngine(workflow, decider, channel.call,
                                lambda event: emit('trace', event=event), router=router, decision_provider=decision_provider, resource_selector=resource_selector)
        if workflow.get('workflow_type') == 'scripted':
            opening = engine.turn('')
            emit('transcript', role='assistant', text=opening['text'], final=True)
            if opening['ended']:
                emit('answer', **opening)
                return
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
            if decision_provider:
                decision_provider.close()


if __name__ == '__main__':
    main()
