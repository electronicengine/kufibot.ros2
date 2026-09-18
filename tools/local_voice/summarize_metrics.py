#!/usr/bin/env python3
"""Summarize JSONL local_ai/metrics events (sessions are separated by their worker UUID).

playback_start is the first PCM submitted to ALSA, not measured acoustic output.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import numpy as np


def summarize(events):
    turns = defaultdict(dict)
    loads = []
    load_at = None
    routing = []
    for event in events:
        if event.get('type') != 'metric':
            continue
        name = event['event']
        if name == 'load_start':
            load_at = event['monotonic_sec']
        elif name == 'load_end' and load_at is not None:
            loads.append(event['monotonic_sec'] - load_at)
        turn = turns[(event.get('session', ''), event['turn'])]
        if name == 'workflow_route_end' and 'workflow_route_start' in turn:
            routing.append(event['monotonic_sec'] - turn['workflow_route_start'])
        turn[name] = event['monotonic_sec']
    pairs = dict(speech_to_playback=('speech_end', 'playback_start'),
                 endpoint_delay=('speech_end', 'endpoint'), stt_finalize=('endpoint', 'stt_final'),
                 llm_first_token=('llm_start', 'llm_first_token'), llm_total=('llm_start', 'llm_end'),
                 tts_first_audio=('tts_start', 'playback_start'),
                 workflow_first_audio=('workflow_start', 'playback_start'),
                 workflow_total=('workflow_start', 'workflow_end'))
    result = {'model_load_sec': loads}
    for name, (start, end) in pairs.items():
        values = [turn[end] - turn[start] for turn in turns.values() if start in turn and end in turn]
        result[name] = dict(count=len(values), p50_sec=float(np.percentile(values, 50)),
                            p95_sec=float(np.percentile(values, 95))) if values else {'count': 0}
    result['workflow_route_call'] = dict(count=len(routing), p50_sec=float(np.percentile(routing, 50)),
        p95_sec=float(np.percentile(routing, 95))) if routing else {'count': 0}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(json.loads(line) for line in args.jsonl.read_text().splitlines()), indent=2))

if __name__ == '__main__':
    main()
