#!/usr/bin/env python3
"""Reproducible real GGUF/Piper serial-vs-stream workflow benchmark (ALSA null)."""
import argparse
import json
import time
from types import SimpleNamespace

from kufibot_interaction import local_voice_worker as worker
from kufibot_interaction.local_voice_runtime import DEFAULTS
from kufibot_interaction.workflow_inference import make_decider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--model', default='/usr/local/ai.models/llamaModel/ufakzeka-1-q8_0.gguf')
    args = parser.parse_args()
    worker.emit = lambda *a, **k: None
    from llama_cpp import Llama
    model = Llama(model_path=args.model, n_ctx=2048, n_threads=3, n_threads_batch=4, verbose=False)
    class Seeded:
        def __getattr__(self, name):
            return getattr(model, name)
        def create_chat_completion(self, **kwargs):
            return model.create_chat_completion(**{**kwargs, 'seed': 17, 'temperature': 0})
    seeded = Seeded()
    config = {**DEFAULTS, 'speaker': 'null', 'tts': '/usr/local/ai.models/trSpeechModel/fettah.onnx'}
    voice = worker.load_voice(config)
    try:
        for repeat in range(args.repeat):
            answers = []
            for mode in (('serial', 'stream') if repeat % 2 == 0 else ('stream', 'serial')):
                model.reset()
                marks = {}
                reporter = SimpleNamespace(set_phase=lambda *a: None,
                    mark=lambda name, **kw: marks.setdefault(name, time.monotonic()))
                generator = (lambda messages: worker.generate_and_speak(config, seeded, messages, voice, reporter)) if mode == 'stream' else None
                decide = make_decider(seeded, 'Türkçe yanıt ver.', reply_generator=generator,
                                      reply_max_tokens=config['llm_max_tokens'])
                started = time.monotonic()
                result = decide({'data': {'prompt': 'Üç kısa cümleyle yanıt ver.'}},
                    {'user': 'Arkadaş canlısı bir robotu anlat.', 'history': []}, [], [], [])
                if mode == 'serial':
                    reporter.mark('llm_end')
                    worker.speak(config, voice, result['text'], reporter)
                answers.append(result['text'])
                print(json.dumps(dict(mode=mode, repeat=repeat,
                    first_pcm_s=round(marks['playback_start']-started, 3),
                    total_s=round(time.monotonic()-started, 3),
                    playback_before_llm_end=marks['playback_start'] < marks['llm_end'],
                    characters=len(result['text']), same_answer=len(answers)==1 or answers[0]==answers[1]), ensure_ascii=False), flush=True)
    finally:
        model.close()


if __name__ == '__main__':
    main()
