"""Stream optional neural TTS from an isolated NumPy/PyTorch interpreter."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import threading
import time
from types import SimpleNamespace

BACKENDS = {'pocket_tts_turkish', 'ema_lightning'}
PACKAGES = {
    'pocket-tts-turkish': ('pocket_tts_turkish', 'Pocket TTS Türkçe'),
    'ema-lightning': ('ema_lightning', 'EMA Lightning'),
}


def package_manifest(path, *, verify=False):
    path = Path(path)
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('backend') not in BACKENDS
            or not isinstance(value.get('files'), dict) or not value['files']):
        raise ValueError('Geçersiz yerel TTS paketi')
    required = ({'config.yaml', 'model.safetensors', 'tokenizer.model', 'voices/' + value.get('voice', 'male_1') + '.wav'}
                if value['backend'] == 'pocket_tts_turkish' else {'ema.pt', 'decoder.pt'})
    if not required.issubset(value['files']):
        raise ValueError('Yerel TTS model dosyaları eksik')
    if value.get('playback_mode', 'streaming') not in ('streaming', 'buffered'):
        raise ValueError('Geçersiz TTS oynatma biçimi')
    if not Path(value.get('runtime_python', '')).is_file() or not os.access(value['runtime_python'], os.X_OK):
        raise ValueError('Ayrı TTS Python ortamı kurulu değil')
    for name, expected in value['files'].items():
        filename = Path(name)
        if filename.is_absolute() or '..' in filename.parts or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError('Geçersiz TTS dosya kaydı')
        target = path.parent / filename
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('Eksik TTS dosyası: ' + name)
        if verify:
            with target.open('rb') as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
            if actual != expected:
                raise ValueError('TTS dosyası doğrulanamadı: ' + name)
    return value


def cache_identity(path):
    path = Path(path)
    files = [path]
    if path.suffix == '.json':
        files += [path.parent / name for name in package_manifest(path)['files']]
    else:
        files.append(Path(str(path) + '.json'))
    return [(str(item), item.stat().st_size, item.stat().st_mtime_ns) for item in files]


class IsolatedVoice:
    """Piper-compatible chunks, bounded pipe buffering and explicit cleanup."""
    def __init__(self, path, threads=1):
        self.path = Path(path).resolve()
        self.manifest = package_manifest(self.path)
        self.threads = max(1, min(4, int(threads)))
        self.process = None
        self.buffer = b''
        self.lock = threading.Lock()
        atexit.register(self.close)
        self._start()

    def _start(self):
        self.buffer = b''
        # Inherit the containing voice worker's process group: its cancellation
        # must terminate both this model and the ALSA/Pulse player.
        self.process = subprocess.Popen(
            [self.manifest['runtime_python'], '-I', str(Path(__file__).with_name('tts_worker.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                 'OMP_NUM_THREADS': str(self.threads), 'OPENBLAS_NUM_THREADS': '1'})
        self._send(dict(manifest=str(self.path), threads=self.threads))
        try:
            header = self._header()
            if not header.get('ready'):
                raise RuntimeError(header.get('error', 'Yerel ses modeli yüklenemedi'))
        except BaseException:
            self.close()
            raise

    def _send(self, value):
        data = memoryview((json.dumps(value) + '\n').encode())
        while data:
            written = self.process.stdin.write(data)
            if not written:
                raise RuntimeError('Yerel ses modeli bağlantısı kapandı')
            data = data[written:]

    def _fill(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
            raise TimeoutError('Yerel ses modeli 120 saniye içinde ses üretmedi')
        data = os.read(self.process.stdout.fileno(), 65536)
        if not data:
            raise RuntimeError('Yerel ses modeli işlemi kapandı')
        self.buffer += data

    def _header(self):
        deadline = time.monotonic() + 120
        while b'\n' not in self.buffer:
            if len(self.buffer) > 65536:
                raise RuntimeError('Geçersiz ses akışı başlığı')
            self._fill(deadline)
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def _audio(self, size):
        deadline = time.monotonic() + 120
        while len(self.buffer) < size:
            self._fill(deadline)
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def synthesize(self, text):
        with self.lock:
            if self.process is None:
                self._start()
            complete = False
            # Pocket's float32 model is slower than playback on a Pi 5 CPU.
            # Buffer each sentence there instead of underrunning the speaker.
            buffered = self.manifest.get('playback_mode',
                'buffered' if self.manifest['backend'] == 'pocket_tts_turkish' else 'streaming') == 'buffered'
            pending = []
            try:
                self._send({'text': text})
                while True:
                    header = self._header()
                    if header.get('error'):
                        raise RuntimeError(header['error'])
                    if header.get('done'):
                        complete = True
                        yield from pending
                        break
                    size, rate = header.get('bytes'), header.get('sample_rate')
                    if (type(size) is not int or not 0 < size <= 4 * 1024 * 1024 or size % 2
                            or rate not in (8000, 16000, 22050, 24000, 44100, 48000)):
                        raise RuntimeError('Geçersiz yerel ses parçası')
                    chunk = SimpleNamespace(sample_rate=rate, sample_channels=1, audio_int16_bytes=self._audio(size))
                    if buffered:
                        pending.append(chunk)
                    else:
                        yield chunk
            finally:
                if not complete:
                    self.close()

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()
