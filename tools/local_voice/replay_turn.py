#!/usr/bin/env python3
"""Exercise the real worker using paced WAV capture and ALSA null playback.

This tests software timing and cleanup; it does not measure acoustic latency.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

from kufibot_interaction import local_voice_worker as worker


def send_pcm(path):
    with wave.open(str(path)) as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Replay WAV must be mono PCM16 / 16 kHz')
        started = time.monotonic()
        count = 0
        while data := stream.readframes(512):
            count += len(data)
            time.sleep(max(0, started + count / 32000 - time.monotonic()))
            os.write(sys.stdout.fileno(), data)
        # Like a live microphone, keep providing silence until the worker closes it.
        while True:
            time.sleep(.032)
            os.write(sys.stdout.fileno(), bytes(1024))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', type=Path, required=True)
    parser.add_argument('--language', choices=['tr', 'en'], default='en')
    parser.add_argument('--backend', choices=['vosk', 'hailo-tiny', 'hailo-base'], default='vosk')
    parser.add_argument('--model', default='/usr/local/ai.models/llamaModel/dolphin3.gguf')
    parser.add_argument('--threads', type=int, default=3)
    parser.add_argument('--batch-threads', type=int, default=4)
    parser.add_argument('--turns', type=int, default=3)
    parser.add_argument('--send-pcm', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.send_pcm:
        send_pcm(args.wav)
        return
    original = subprocess.Popen
    def launch(command, **kwargs):
        if command[0] == 'arecord':
            command = [sys.executable, str(Path(__file__).resolve()), '--send-pcm', '--wav', str(args.wav.resolve())]
        return original(command, **kwargs)
    worker.subprocess.Popen = launch
    root = '/usr/local/ai.models/'
    config = dict(language=args.language, llm=args.model,
                  stt=root+('trRecognizeModel' if args.language == 'tr' else 'engRecognizeModel'),
                  tts=root+('trSpeechModel/fettah.onnx' if args.language == 'tr' else 'engSpeechModel/en_GB-alan-low.onnx'),
                  system_prompt='You are a friendly robot. Reply in one short sentence.',
                  mic='replay', speaker='null', llm_threads=args.threads,
                  llm_batch_threads=args.batch_threads, llm_max_tokens=48)
    if args.backend.startswith('hailo-'):
        config.update(stt_backend='hailo_whisper', stt=root+'whisper-'+args.backend.split('-')[1]+'-hailo8l')
    try:
        worker.run(config, max_turns=args.turns)
    finally:
        worker.subprocess.Popen = original

if __name__ == '__main__':
    main()
