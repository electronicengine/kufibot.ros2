#!/usr/bin/env python3
"""Compare fixed WAVs with baseline Vosk, VAD/Vosk or VAD/Hailo; no mic/speaker use."""
import argparse
import json
from pathlib import Path
import re
import resource
import time
import wave

from kufibot_interaction.local_voice_runtime import DEFAULTS, SileroVad, SpeechGate
from kufibot_interaction.local_voice_worker import VoskBackend


def word_errors(reference, actual):
    clean = lambda value: re.findall(r'\w+', value.casefold())
    expected, received = clean(reference), clean(actual)
    previous = list(range(len(received) + 1))
    for index, word in enumerate(expected, 1):
        current = [index]
        for j, other in enumerate(received, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (word != other)))
        previous = current
    return previous[-1], len(expected)


def transcribe(pcm, backend, vad, baseline=False):
    backend.reset()
    gate = SpeechGate(DEFAULTS)
    if vad:
        vad.reset()
    parts, endpoints = [], []
    frame_bytes = 4000 if baseline else 1024
    for offset in range(0, len(pcm), frame_bytes):
        frame = pcm[offset:offset+frame_bytes].ljust(frame_bytes, b'\0')
        audio_at = (offset + frame_bytes) / 32000
        if baseline:
            recognizer = backend.recognizer
            if recognizer.AcceptWaveform(frame):
                text = json.loads(recognizer.Result()).get('text', '').strip()
                if text:
                    parts.append(text)
                    endpoints.append(audio_at)
            continue
        frames, done = gate.push(frame, vad(frame), audio_at)
        for item in frames:
            backend.feed(item)
        if done:
            endpoints.append(audio_at)
            parts.append(backend.finish(trailing_silence_ms=gate.silent * 32))
            backend.reset()
            gate = SpeechGate(DEFAULTS)
    if baseline:
        parts.append(json.loads(backend.recognizer.FinalResult()).get('text', ''))
    elif gate.active:
        parts.append(backend.finish())
    return ' '.join(p for p in parts if p), endpoints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--backend', choices=['baseline', 'vosk', 'hailo-tiny', 'hailo-base'], required=True)
    parser.add_argument('--language', choices=['tr', 'en'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    samples = [s for s in json.loads(args.manifest.read_text()) if s['language'] == args.language]
    start = time.monotonic()
    if args.backend.startswith('hailo'):
        from kufibot_interaction.hailo_whisper import HailoWhisper
        backend = HailoWhisper('/usr/local/ai.models/whisper-' + args.backend.split('-')[1] + '-hailo8l', args.language)
    else:
        backend = VoskBackend('/usr/local/ai.models/' + ('trRecognizeModel' if args.language == 'tr' else 'engRecognizeModel'))
    vad = None if args.backend == 'baseline' else SileroVad(DEFAULTS['vad_model'])
    load_sec = time.monotonic() - start
    try:
        with args.output.open('w') as output:
            for repeat in range(args.repeat):
                for sample in samples:
                    with wave.open(str(args.manifest.parent / sample['wav']), 'rb') as stream:
                        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
                            raise ValueError('Benchmark WAV must be 16 kHz mono PCM16')
                        pcm = stream.readframes(stream.getnframes())
                    started, cpu = time.monotonic(), time.process_time()
                    try:
                        text, endpoints = transcribe(pcm, backend, vad, args.backend == 'baseline')
                        errors, words = word_errors(sample['text'], text)
                        result = dict(hypothesis=text, word_errors=errors, reference_words=words,
                                      false_activation=not sample['text'] and bool(text), endpoint_audio_sec=endpoints)
                    except Exception as error:
                        result = dict(error=str(error))
                    elapsed = time.monotonic() - started
                    record = dict(**sample, **result, backend=args.backend, repeat=repeat, load_sec=load_sec,
                                  processing_sec=elapsed, cpu_sec=time.process_time()-cpu,
                                  peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                  audio_sec=len(pcm)/32000, rtf=elapsed/(len(pcm)/32000))
                    output.write(json.dumps(record, ensure_ascii=False) + '\n')
                    output.flush()
                    print(sample['id'], args.backend, result, flush=True)
    finally:
        backend.close()

if __name__ == '__main__':
    main()
