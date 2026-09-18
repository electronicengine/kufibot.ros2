#!/usr/bin/env python3
"""Create an auditable summary from benchmark_stt/llm and replay JSONL files."""
import argparse
import json
from pathlib import Path
import statistics

from summarize_metrics import summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(stt={}, llm={}, replay={},
                  limitations=['Synthetic Piper corpus; not a human speech accuracy evaluation.',
                               'Replay uses paced file input and ALSA null; not acoustic latency.',
                               'No simultaneous camera/ROS workload during the timing measurements.'])
    records = {}
    for path in sorted(args.results.glob('*.jsonl')):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        records[path.name] = rows
        if path.name.startswith('stt-'):
            words = sum(r.get('reference_words', 0) for r in rows)
            errors = sum(r.get('word_errors', 0) for r in rows)
            report['stt'][path.stem] = dict(count=len(rows), reference_words=words, word_errors=errors,
                wer=errors / words if words else None, failures=sum('error' in r for r in rows),
                false_activations=sum(r.get('false_activation', False) for r in rows),
                processing_sec=sum(r['processing_sec'] for r in rows), cpu_sec=sum(r['cpu_sec'] for r in rows),
                rtf=sum(r['processing_sec'] for r in rows) / sum(r['audio_sec'] for r in rows))
        elif path.name.startswith('llm-'):
            report['llm'][path.stem] = dict(count=len(rows), **{
                k: statistics.median(r[k] for r in rows) for k in ('first_token_sec', 'total_sec', 'cpu_sec', 'temperature_c')})
        elif path.name.startswith('replay-'):
            report['replay'][path.stem] = summarize(rows)
    gains = []
    for language in ('tr', 'en'):
        baseline = {r['id']: r for r in records[f'stt-baseline-{language}.jsonl']}
        for row in records[f'stt-vosk-{language}.jsonl']:
            old, new = baseline[row['id']].get('endpoint_audio_sec', []), row.get('endpoint_audio_sec', [])
            if len(old) == len(new) == 1:
                gains.append(old[0] - new[0])
    report['paired_endpoint_gain_sec'] = dict(count=len(gains), median=statistics.median(gains))
    args.output.write_text(json.dumps(dict(summary=report, records=records), ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
