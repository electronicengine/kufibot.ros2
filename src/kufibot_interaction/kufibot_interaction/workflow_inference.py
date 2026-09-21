"""Node-only chat prompts and correlated worker tool requests."""
import json
import select
import sys
import time
import uuid


def make_decider(llm, system_prompt='', language='tr', *, reply_generator=None,
                 reply_max_tokens=256, metric=lambda *args: None):
    """Reply only. Legacy global prompt/language arguments do not alter node instructions."""
    formatter = None
    def decide(node, context, tools, collections, exits):
        nonlocal formatter
        node_prompt = node['data'].get('prompt', node['data'].get('message', ''))
        result = context.get('last_result', {})
        # Verbatim answers are explicitly configured on the node, never inferred
        # from the model's tokenizer. Empty results go to the node's own prompt.
        if not context.get('_require_llm', False) and node['data'].get('answer_mode') == 'quote' and isinstance(result, dict) and result.get('results'):
            match = result['results'][0]
            excerpt, location = match['text'], match.get('location', '')
            prefix = location + ': '
            if location.startswith('$') and excerpt.startswith(prefix):
                try:
                    scalar = json.loads(excerpt[len(prefix):])
                    excerpt = scalar if isinstance(scalar, str) else str(scalar)
                except ValueError:
                    pass
            return {'action': 'reply', 'text': excerpt}
        from .local_voice_worker import make_formatter
        if formatter is None:
            formatter = make_formatter(llm)
        reply_messages = []
        for turn in context.get('history', [])[-2:]:
            reply_messages.extend([{'role': 'user', 'content': turn['user']},
                                   {'role': 'assistant', 'content': turn['assistant']}])
        # The active node owns the system instruction on every turn. Routing
        # must never replace the user's utterance with the target node's prompt.
        system_messages = [{'role': 'system', 'content': node_prompt}] if node_prompt.strip() else []
        user_text = str(context.get('user', ''))
        evidence = json.dumps({'tool_result': context['last_result']}, ensure_ascii=False) if 'last_result' in context else ''
        while True:
            content = user_text + ('\n\n' + evidence if evidence else '')
            candidate = system_messages + reply_messages + [{'role': 'user', 'content': content}]
            if len(llm.tokenize(formatter(messages=candidate).prompt.encode(), add_bos=False, special=True)) <= llm.n_ctx() - reply_max_tokens - 8:
                break
            if reply_messages:
                del reply_messages[:2]
            elif len(evidence) > 128:
                evidence = evidence[:len(evidence) * 3 // 4]
            else:
                raise ValueError('Workflow talimatları ve kullanıcı mesajı model bağlamını aşıyor')
        if reply_generator is not None:
            text = reply_generator(candidate)
        else:
            answer = llm.create_chat_completion(messages=candidate, max_tokens=reply_max_tokens, temperature=.3,
                top_k=40, top_p=.9, min_p=0, repeat_penalty=1)
            text = answer['choices'][0]['message']['content'] or ''
            from .speech_guard import validate_speech
            validate_speech(text, candidate)
        return {'action': 'reply', 'text': text, 'request_text': user_text}
    return decide


class ToolChannel:
    def __init__(self, emit, session_id):
        self.emit, self.session_id, self.turn_id = emit, session_id, 0

    def call(self, name, arguments):
        call_id = uuid.uuid4().hex
        identity = dict(session_id=self.session_id, turn_id=self.turn_id, call_id=call_id)
        self.emit('workflow_tool', name=name, arguments=arguments, **identity)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not select.select([sys.stdin], [], [], max(0, deadline-time.monotonic()))[0]:
                break
            line = sys.stdin.readline()
            if not line:
                raise RuntimeError('Workflow kanalı kapandı')
            reply = json.loads(line)
            if all(reply.get(k) == v for k, v in identity.items()):
                return reply['result']
        raise TimeoutError('Araç çağrısı 10 saniyede tamamlanmadı')
