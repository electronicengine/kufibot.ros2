#!/usr/bin/env python3
"""Measure prompt/decode thread combinations on the selected GGUF without changing settings."""
import argparse
import json
from pathlib import Path
import resource
import time

from kufibot_interaction.local_voice_worker import make_formatter, fit_messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--threads', type=int, required=True)
    parser.add_argument('--batch-threads', type=int, required=True)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--max-tokens', type=int, default=48)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from llama_cpp import Llama
    at = time.monotonic()
    llm = Llama(model_path=args.model, n_ctx=2048, n_threads=args.threads,
                n_threads_batch=args.batch_threads, verbose=False)
    formatter = make_formatter(llm)
    load_sec = time.monotonic() - at
    try:
        with args.output.open('w') as stream:
            for repeat in range(args.repeat):
                for language, prompt in [('en', 'Tell me a short story about a friendly robot.'),
                                         ('tr', 'Arkadaş canlısı bir robot hakkında kısa bir hikaye anlat.')]:
                    llm.reset()
                    system = dict(role='system', content=f'Reply briefly in {language}.')
                    messages = fit_messages(llm, formatter, system, [], prompt, args.max_tokens)
                    started, cpu = time.monotonic(), time.process_time()
                    parts, first = [], None
                    for chunk in llm.create_chat_completion(messages=messages, max_tokens=args.max_tokens,
                                                            temperature=0, stream=True, seed=17):
                        text = chunk['choices'][0]['delta'].get('content') or ''
                        if text:
                            first = first if first is not None else time.monotonic()
                            parts.append(text)
                    answer = ''.join(parts)
                    end = time.monotonic()
                    record = dict(model=args.model, threads=args.threads, batch_threads=args.batch_threads,
                                  repeat=repeat, language=language, load_sec=load_sec,
                                  first_token_sec=None if first is None else first-started,
                                  total_sec=end-started, cpu_sec=time.process_time()-cpu,
                                  output_tokens=len(llm.tokenize(answer.encode(), add_bos=False)),
                                  temperature_c=int(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,
                                  peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, answer=answer)
                    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                    stream.flush()
                    print(args.threads, args.batch_threads, repeat, language, round(record['total_sec'], 3), flush=True)
    finally:
        llm.close()

if __name__ == '__main__':
    main()
