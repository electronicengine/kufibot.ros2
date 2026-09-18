#!/usr/bin/env python3
"""Compare full-response and sentence-stream playback using real LLM/Piper.

ALSA null measures first PCM submission, not acoustic output. Generation is
seeded with temperature zero; each run clears LLM context and uses the same voice.
"""
import argparse
import json
from pathlib import Path
import time

from kufibot_interaction import local_voice_worker as worker
from kufibot_interaction.local_voice_runtime import DEFAULTS, Reporter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='/usr/local/ai.models/llamaModel/dolphin3.gguf')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=2)
    args = parser.parse_args()
    reporter = Reporter(worker.emit)  # Fork heartbeat before loading native libraries.
    from llama_cpp import Llama
    llm = None
    try:
        reporter.set_phase('loading')
        llm = Llama(model_path=args.model, n_ctx=2048, n_threads=3, n_threads_batch=4, verbose=False)
        formatter = worker.make_formatter(llm)
        class SeededModel:
            tokenize = llm.tokenize
            def create_chat_completion(self, **kwargs):
                return llm.create_chat_completion(**{**kwargs, 'temperature': 0, 'seed': 17})
        seeded = SeededModel()
        rows = []
        for language in ('en', 'tr'):
            config = {**DEFAULTS, 'speaker': 'null', 'tts_threads': 1, 'llm_max_tokens': 96,
                      'tts': '/usr/local/ai.models/' + ('engSpeechModel/en_GB-alan-low.onnx' if language == 'en' else 'trSpeechModel/fettah.onnx')}
            voice = worker.load_voice(config)
            system = {'role': 'system', 'content': f'Reply in {language} with exactly three short sentences. End each sentence with a period.'}
            prompt = 'Tell me about a friendly robot.' if language == 'en' else 'Arkadaş canlısı bir robot hakkında bilgi ver.'
            messages = worker.fit_messages(llm, formatter, system, [], prompt, config['llm_max_tokens'])
            for repeat in range(args.repeat):
                for mode in (('serial', 'stream') if repeat % 2 == 0 else ('stream', 'serial')):
                    llm.reset()
                    reporter.turn += 1
                    events = []
                    original_mark = reporter.mark
                    def mark(name, **extra):
                        events.append({'event': name, 'at': time.monotonic()})
                        original_mark(name, **extra)
                    reporter.mark = mark
                    started = time.monotonic()
                    try:
                        if mode == 'stream':
                            answer = worker.generate_and_speak(config, seeded, messages, voice, reporter)
                        else:
                            reporter.set_phase('thinking')
                            reporter.mark('llm_start')
                            parts = []
                            for item in seeded.create_chat_completion(messages=messages, max_tokens=config['llm_max_tokens'], stream=True):
                                content = item['choices'][0]['delta'].get('content') or ''
                                if content:
                                    if not parts:
                                        reporter.mark('llm_first_token')
                                    parts.append(content)
                            answer = ''.join(parts)
                            reporter.mark('llm_end')
                            worker.speak(config, voice, answer, reporter)
                        timing = {e['event']: e['at'] for e in events}
                        row = dict(language=language, repeat=repeat, mode=mode, answer=answer,
                                   first_pcm_sec=timing['playback_start']-started,
                                   llm_sec=timing['llm_end']-timing['llm_start'],
                                   playback_before_llm_end=timing['playback_start'] < timing['llm_end'],
                                   total_sec=time.monotonic()-started)
                        rows.append(row)
                        args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n')
                    finally:
                        reporter.mark = original_mark
    finally:
        if llm:
            llm.close()
        reporter.close()

if __name__ == '__main__':
    main()
