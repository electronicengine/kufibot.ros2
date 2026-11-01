"""Standalone isolated TTS worker; JSON headers followed by raw mono S16 PCM."""
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys


def load_backend(manifest_path, threads):
    import torch
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    if manifest['backend'] == 'pocket_tts_turkish':
        from pocket_tts_turkish import TurkishTTS
        model = TurkishTTS.from_pretrained(str(path.parent), device='cpu')
        torch.set_num_threads(threads)
        return model.sample_rate, lambda text: model.stream(text, voice=manifest.get('voice', 'male_1'))
    if manifest['backend'] == 'ema_lightning':
        # Pinned EMA 1.1.2 supports a parts constructor. Loading explicit local
        # files avoids the public constructor's implicit Hub/main downloads.
        from ema_lightning.api import EMA, Frontend, load_acoustic, load_decoder
        device = torch.device('cpu')
        acoustic = load_acoustic(str(path.parent / 'ema.pt'), device)
        decoder = load_decoder(str(path.parent / 'decoder.pt'), device)
        model = EMA._from_parts(acoustic, decoder, Frontend(acoustic.vocab), device)
        # One robot speaks one utterance at a time. EMA's automatic CPU batch
        # search benchmarks many concurrent callers before the first audio.
        model._batch_size = 1
        return 48000, model.stream
    raise ValueError('Desteklenmeyen yerel ses modeli')


def main():
    os.environ['HF_HUB_OFFLINE'] = '1'
    output = sys.stdout.buffer

    def send(header, data=b''):
        output.write((json.dumps(header) + '\n').encode())
        if data:
            output.write(data)
        output.flush()

    try:
        config = json.loads(sys.stdin.readline())
        with redirect_stdout(sys.stderr):
            import numpy as np
            rate, stream = load_backend(config['manifest'], config.get('threads', 1))
        send({'ready': True})
        for line in sys.stdin:
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                for chunk in stream(request['text']):
                    audio = np.nan_to_num(np.asarray(chunk, dtype=np.float32).reshape(-1))
                    if not len(audio):
                        continue
                    pcm = (np.clip(audio, -1, 32767 / 32768) * 32768).astype('<i2').tobytes()
                    send({'bytes': len(pcm), 'sample_rate': rate}, pcm)
            send({'done': True})
    except Exception as error:
        send({'error': str(error)})
        sys.exit(1)


if __name__ == '__main__':
    main()
