#!/usr/bin/env python3
"""Generate clearly labelled synthetic TR/EN smoke fixtures, never a human WER claim."""
import argparse
import json
import math
from pathlib import Path
import wave
import numpy as np
from scipy.signal import resample_poly
from kufibot_interaction.local_voice_worker import load_voice


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    specs = {
        'tr': ('trSpeechModel/fettah.onnx', ['Dur.', 'Merhaba robot. Bugün nasılsın?',
               'Bana kısa bir hikaye anlat. Sonra birlikte yeni bir oyun oynayalım.']),
        'en': ('engSpeechModel/en_GB-alan-low.onnx', ['Stop.', 'Hello robot. How are you today?',
               'Tell me a short story. Then we can play a new game together.'])}
    manifest = []
    rng = np.random.default_rng(17)
    for language, (model, texts) in specs.items():
        voice = load_voice({'tts': '/usr/local/ai.models/' + model, 'tts_threads': 2})
        for scenario in ('short', 'clean', 'noise', 'pause', 'long', 'silence'):
            text = texts[0 if scenario == 'short' else 2 if scenario in ('long', 'pause') else 1]
            if scenario == 'silence':
                text, audio = '', np.zeros(32000, dtype=np.float32)
            else:
                chunks = list(voice.synthesize(text))
                pieces = []
                for chunk in chunks:
                    divisor = math.gcd(chunk.sample_rate, 16000)
                    pieces.append(resample_poly(chunk.audio_float_array, 16000 // divisor,
                                                chunk.sample_rate // divisor))
                    if scenario == 'pause':
                        pieces.append(np.zeros(3200, dtype=np.float32))
                audio = np.concatenate(pieces)
                if scenario == 'long':
                    audio = np.tile(audio, 3)
                    text = ' '.join([text] * 3)
                if scenario == 'noise':
                    audio = audio + rng.normal(0, .008, audio.shape).astype(np.float32)
            speech_end = (len(audio) + 4800) / 16000 if text else None
            audio = np.concatenate((np.zeros(4800), audio, np.zeros(16000)))
            pcm = (np.clip(audio, -1, 1) * 32767).astype('<i2')
            filename = f'{language}-{scenario}.wav'
            with wave.open(str(args.output / filename), 'wb') as stream:
                stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                stream.writeframes(pcm.tobytes())
            manifest.append(dict(id=f'{language}-{scenario}', language=language, scenario=scenario,
                                 wav=filename, text=text, speech_end_sec=speech_end, source='synthetic_piper'))
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')

if __name__ == '__main__':
    main()
