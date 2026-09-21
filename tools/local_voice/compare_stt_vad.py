#!/usr/bin/env python3
"""Record one uncut sample and compare Vosk with and without Silero gating."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/kufibot_interaction'))
from kufibot_interaction.audio_devices import audio_command
from kufibot_interaction.local_voice_runtime import DEFAULTS, SileroVad, SpeechGate
from kufibot_interaction.local_voice_worker import VoskBackend
from kufibot_interaction.vad_trace import frame_trace


def capture(device, seconds, rate=16000):
    data = bytearray()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(audio_command(True, device, rate, 1), stdout=subprocess.PIPE,
                                   stderr=errors, bufsize=0)
        try:
            while len(data) < seconds * rate * 2:
                if not select.select([process.stdout], [], [], 5)[0]:
                    raise RuntimeError('Mikrofondan 5 saniyedir ses verisi gelmiyor')
                chunk = os.read(process.stdout.fileno(), min(4096, seconds * rate * 2 - len(data)))
                if not chunk:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode(errors='replace'))
                if not data:
                    print(f'DİNLİYOR — şimdi konuşun; {seconds} saniye kesintisiz kayıt.', flush=True)
                data.extend(chunk)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
    return bytes(data)


def recognize(pcm, backend, config, vad=None, trace=None):
    backend.reset()
    gate = SpeechGate(config)
    parts = []
    admitted = 0
    if vad:
        vad.reset()
    for offset in range(0, len(pcm), 1024):
        frame = pcm[offset:offset + 1024].ljust(1024, b'\0')
        was_active = gate.active
        probability = vad(frame) if vad else 0
        frames, done = gate.push(frame, probability, offset / 32000) if vad else ([frame], False)
        if vad and trace is not None:
            row = frame_trace(frame, probability, gate, was_active, frames, done, offset // 1024)
            trace.write(json.dumps(row) + '\n')
            if row['event'] != 'frame':
                print(f"  VAD {row['event']}: t={row['audio_start_sec']:.3f}s "
                      f"p={row['probability']:.3f} STT başlangıcı={row['retained_from_sec']} "
                      f"bitiş={row['endpoint_reason']}", flush=True)
        for item in frames:
            admitted += len(item)
            backend.feed(item)
        if done:
            parts.append(backend.finish())
            backend.reset()
            gate = SpeechGate(config)
            if vad:
                vad.reset()
    parts.append(backend.finish())
    return dict(text=' '.join(p for p in parts if p), stt_audio_seconds=admitted / 32000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=8)
    parser.add_argument('--wav', type=Path, help='Use an existing uncut recording')
    parser.add_argument('--language', choices=['tr', 'en'], default='tr')
    parser.add_argument('--capture-rate', type=int, choices=[16000, 32000], default=16000,
                        help='32000: capture at camera native rate, then resample locally to 16 kHz')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error('seconds must be between 1 and 30')
    import yaml
    from vosk import SetLogLevel
    SetLogLevel(-1)
    params = yaml.safe_load((ROOT / 'src/kufibot_bringup/config/interactive_robot.yaml').read_text())['voice_agent_node']['ros__parameters']
    config = {**DEFAULTS, **{k[6:]: v for k, v in params.items() if k.startswith('local_')}}
    folder = ROOT / 'log/stt-compare' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True)
    if args.wav:
        with wave.open(str(args.wav), 'rb') as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (16000, 1, 2, 'NONE'):
                raise ValueError('16 kHz mono PCM16 WAV gerekli')
            pcm = source.readframes(source.getnframes())
    else:
        print('Hazırlanın. 3 saniye sonra kayıt başlayacak.', flush=True)
        time.sleep(3)
        pcm = capture(params['mic_device'], args.seconds, args.capture_rate)
        with wave.open(str(folder / 'native.wav'), 'wb') as output:
            output.setparams((1, 2, args.capture_rate, 0, 'NONE', 'not compressed'))
            output.writeframes(pcm)
        if args.capture_rate != 16000:
            import numpy as np
            from scipy.signal import resample_poly
            samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64)
            samples = resample_poly(samples, 1, args.capture_rate // 16000)
            pcm = np.clip(np.rint(samples), -32768, 32767).astype('<i2').tobytes()
    with wave.open(str(folder / 'raw.wav'), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(pcm)
    print('Kayıt tamamlandı; aynı ses üç şekilde çözümleniyor.', flush=True)
    model = '/usr/local/ai.models/' + ('trRecognizeModel' if args.language == 'tr' else 'engRecognizeModel')
    backend = VoskBackend(model)
    results = dict(model=model, mic=params['mic_device'], config=config,
                   capture_rate=None if args.wav else args.capture_rate,
                   source_wav=str(args.wav) if args.wav else None, results={})
    try:
        vad = SileroVad(config['vad_model'])
        for index, (label, options, detector) in enumerate([('VAD kapalı', config, None),
                                         ('VAD mevcut ayarlar', config, vad),
                                         ('VAD 1500 ms tampon', {**config, 'vad_pre_roll_ms': 1500}, vad)]):
            with (folder / f'vad-{index}.jsonl').open('w') as trace:
                result = recognize(pcm, backend, options, detector, trace)
            results['results'][label] = result
            print(f"{label}: {result['text']!r} (STT'ye {result['stt_audio_seconds']:.2f} saniye)", flush=True)
    finally:
        backend.close()
    (folder / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f'Ham kayıt ve sonuçlar: {folder}', flush=True)


if __name__ == '__main__':
    main()
