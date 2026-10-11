"""Offline streaming Turkish Zipformer STT, using the robot's VAD endpoint."""
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np

MODEL_ID = 'turkish-stt-zipformer'
BACKEND = 'sherpa_zipformer'
SAMPLE_RATE = 16000


def package_manifest(folder, verify=False):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text())
    if (not isinstance(manifest, dict) or manifest.get('backend') != BACKEND or manifest.get('sample_rate') != SAMPLE_RATE
            or manifest.get('languages') != ['tr'] or manifest.get('precision') != 'int8'
            or manifest.get('decoding_method') not in ('greedy_search', 'modified_beam_search')
            or type(manifest.get('max_active_paths')) is not int or not 1 <= manifest['max_active_paths'] <= 32
            or type(manifest.get('num_threads')) is not int or not 1 <= manifest['num_threads'] <= 8):
        raise ValueError('Geçersiz Türkçe Zipformer paket manifesti')
    components = manifest.get('components', {})
    if not isinstance(components, dict) or set(components) != {'encoder', 'decoder', 'joiner', 'tokens'}:
        raise ValueError('Zipformer encoder, decoder, joiner ve tokens dosyaları gerekli')
    files = manifest.get('files', {})
    if not isinstance(files, dict):
        raise ValueError('Zipformer dosya özetleri gerekli')
    for component, name in components.items():
        suffix = '.txt' if component == 'tokens' else '.int8.onnx'
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(suffix):
            raise ValueError('Geçersiz Zipformer bileşen dosyası')
        checksum = files.get(name, '')
        path = folder / name
        if not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum) or not path.is_file():
            raise ValueError('Eksik Zipformer dosyası: ' + name)
        if verify:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != checksum:
                raise ValueError('Zipformer dosya özeti uyuşmuyor: ' + name)
    return manifest


class SherpaZipformer:
    def __init__(self, folder, language='tr'):
        if language != 'tr':
            raise ValueError('Türkçe Zipformer yalnız tr dilini destekler')
        manifest = package_manifest(folder, verify=True)
        import sherpa_onnx
        components = {name: str(Path(folder) / path) for name, path in manifest['components'].items()}
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            **components, num_threads=manifest['num_threads'], provider='cpu',
            decoding_method=manifest['decoding_method'], max_active_paths=manifest['max_active_paths'],
            blank_penalty=0.0, enable_endpoint_detection=False)
        self.reset()

    def reset(self):
        self.stream = self.recognizer.create_stream()
        self.finished = False
        self.result = ''
        self.audio_samples = 0
        self.inference_sec = 0.
        self.timings = {}

    def _decode(self):
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

    def feed(self, pcm):
        if self.finished:
            raise ValueError('STT turu tamamlandı; önce reset çağırın')
        if len(pcm) % 2:
            raise ValueError('STT girdisi 16 kHz mono PCM16 olmalı')
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.
        if samples.size:
            started = time.monotonic()
            self.stream.accept_waveform(SAMPLE_RATE, samples)
            self._decode()
            self.audio_samples += samples.size
            self.inference_sec += time.monotonic()-started

    def partial(self):
        if self.finished:
            return self.result
        result = self.recognizer.get_result(self.stream)
        return (result if isinstance(result, str) else result.text).strip()

    def finish(self, trailing_silence_ms=0):
        if self.finished:
            return self.result
        started = time.monotonic()
        if self.audio_samples:
            # Upstream flushes one second of silence to release the final chunk.
            self.stream.accept_waveform(SAMPLE_RATE, np.zeros(SAMPLE_RATE, dtype=np.float32))
            self.stream.input_finished()
            self._decode()
            self.result = self.partial()
        self.finished = True
        self.inference_sec += time.monotonic()-started
        self.timings = {'stt_backend': BACKEND, 'inference_sec': round(self.inference_sec, 4),
                        'stt_audio_seconds': self.audio_samples / SAMPLE_RATE}
        return self.result

    def close(self):
        self.stream = self.recognizer = None
