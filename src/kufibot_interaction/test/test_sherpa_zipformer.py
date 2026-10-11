import hashlib
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from kufibot_interaction import ai_settings as ai
from kufibot_interaction.local_voice_worker import create_stt
from kufibot_interaction.sherpa_zipformer import BACKEND, MODEL_ID, package_manifest


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / MODEL_ID
    folder.mkdir()
    components = {kind: kind + '.int8.onnx' for kind in ('encoder', 'decoder', 'joiner')}
    components['tokens'] = 'tokens.txt'
    files = {}
    for name in components.values():
        data = name.encode()
        (folder / name).write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    manifest = dict(backend=BACKEND, sample_rate=16000, languages=['tr'], precision='int8',
        num_threads=3, decoding_method='modified_beam_search', max_active_paths=12,
        components=components, files=files)
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    return folder, manifest


def test_discovery_supplements_custom_catalog_and_missing_component_is_unavailable(package, monkeypatch):
    folder, manifest = package
    monkeypatch.setenv('KUFIBOT_AI_MODEL_ROOT', str(folder.parent))
    monkeypatch.setenv('KUFIBOT_AI_CATALOG', str(folder.parent / 'catalog.json'))
    old = folder.parent / 'vosk'
    old.mkdir()
    (old / 'final.mdl').touch()
    ai.catalog_path().write_text(json.dumps([dict(id='old', kind='stt', languages=['tr'], path=str(old))]))
    models = {model['id']: model for model in ai.catalog()}
    assert models['old']['backend'] == 'vosk' and models['old']['available']
    assert models[MODEL_ID]['backend'] == BACKEND and models[MODEL_ID]['available']
    assert models[MODEL_ID]['languages'] == ['tr']
    (folder / manifest['components']['encoder']).unlink()
    assert not next(model for model in ai.catalog() if model['id'] == MODEL_ID)['available']


def test_integrity_failure_never_loads_corrupt_model(package, monkeypatch):
    folder, manifest = package
    (folder / manifest['components']['joiner']).write_bytes(b'corrupt')
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace())
    with pytest.raises(ValueError, match='özeti uyuşmuyor'):
        create_stt({'stt_backend': BACKEND, 'stt': str(folder), 'language': 'tr'})


@pytest.mark.parametrize('change', [dict(languages=['en']), dict(sample_rate=8000),
    dict(num_threads=True), dict(max_active_paths=0), dict(precision='fp32')])
def test_invalid_package_rejected(package, change):
    folder, manifest = package
    (folder / 'manifest.json').write_text(json.dumps({**manifest, **change}))
    with pytest.raises(ValueError, match='manifest'):
        package_manifest(folder)


def test_manifest_rejects_external_component_path(package):
    folder, manifest = package
    manifest['components']['encoder'] = '../encoder.int8.onnx'
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='bileşen'):
        package_manifest(folder)


def test_streaming_pcm_partial_flush_reset_and_cleanup(package, monkeypatch):
    folder, _ = package
    class Stream:
        def __init__(self):
            self.chunks = []
            self.ready = self.finished = False
        def accept_waveform(self, rate, samples):
            assert rate == 16000
            self.chunks.append(samples.copy())
            self.ready = True
        def input_finished(self):
            self.finished = True
    class Recognizer:
        def create_stream(self):
            return Stream()
        def is_ready(self, stream):
            return stream.ready
        def decode_stream(self, stream):
            stream.ready = False
        def get_result(self, stream):
            return SimpleNamespace(text=' merhaba robot ' if stream.chunks else '')
    options = []
    monkeypatch.setitem(sys.modules, 'sherpa_onnx', SimpleNamespace(OnlineRecognizer=SimpleNamespace(
        from_transducer=lambda **kw: options.append(kw) or Recognizer())))
    backend = create_stt({'stt_backend': BACKEND, 'stt': str(folder), 'language': 'tr'})
    assert options[0]['provider'] == 'cpu' and not options[0]['enable_endpoint_detection']
    assert options[0]['decoding_method'] == 'modified_beam_search'
    backend.feed(np.array([-32768, 0, 16384, 32767], dtype='<i2').tobytes())
    np.testing.assert_allclose(backend.stream.chunks[0], [-1., 0., .5, 32767 / 32768])
    assert backend.partial() == 'merhaba robot'
    assert backend.finish(trailing_silence_ms=600) == 'merhaba robot'
    assert backend.stream.finished
    assert len(backend.stream.chunks[-1]) == 16000
    assert backend.finish() == 'merhaba robot' and len(backend.stream.chunks) == 2
    assert backend.timings['stt_audio_seconds'] == 4 / 16000
    previous = backend.stream
    backend.reset()
    assert backend.stream is not previous and backend.partial() == ''
    assert backend.finish() == ''
    backend.reset()
    with pytest.raises(ValueError, match='PCM16'):
        backend.feed(b'x')
    backend.close()
    assert backend.stream is backend.recognizer is None


def test_wrong_language_is_rejected_without_model_loading(package):
    folder, _ = package
    with pytest.raises(ValueError, match='yalnız tr'):
        create_stt({'stt_backend': BACKEND, 'stt': str(folder), 'language': 'en'})
