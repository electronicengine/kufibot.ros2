import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from kufibot_interaction import ai_settings, tts_backends
from kufibot_interaction.activation_worker import cached_cues
from kufibot_interaction.local_voice_worker import load_voice


def package(root, backend='ema_lightning'):
    root.mkdir(parents=True, exist_ok=True)
    files = ['ema.pt', 'decoder.pt'] if backend == 'ema_lightning' else [
        'config.yaml', 'model.safetensors', 'tokenizer.model', 'voices/male_1.wav']
    hashes = {}
    for name in files:
        target = root / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(b'model data')
        hashes[name] = hashlib.sha256(b'model data').hexdigest()
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps(dict(schema_version=1, backend=backend,
        runtime_python=sys.executable, files=hashes, voice='male_1')))
    return manifest


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    script = tmp_path / 'fake_tts.py'
    script.write_text('''
import json,sys,time
output=sys.stdout.buffer
def send(value,data=b''):
    output.write((json.dumps(value)+'\\n').encode()+data)
    output.flush()
config=json.loads(sys.stdin.readline())
send({'ready':True})
for line in sys.stdin:
    text=json.loads(line)['text']
    if text=='error':
        send({'error':'model failure'})
        continue
    send({'bytes':4000,'sample_rate':24000},b'\\x0a\\x00'*2000)
    time.sleep(.05)
    if text=='late_error':
        send({'error':'generation failed after first chunk'})
        continue
    send({'bytes':8000,'sample_rate':24000},b'\\x02\\x00'*4000)
    send({'done':True})
''')
    popen = subprocess.Popen
    def isolated_process(arguments, **kwargs):
        assert arguments[1] == '-I'
        return popen([*arguments[:-1], str(script)], **kwargs)
    monkeypatch.setattr(tts_backends.subprocess, 'Popen', isolated_process)
    return package(tmp_path / 'ema-lightning')


def test_catalog_discovers_both_backends_without_touching_piper(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_AI_MODEL_ROOT', str(tmp_path))
    monkeypatch.setenv('KUFIBOT_AI_CATALOG', str(tmp_path / 'catalog.json'))
    # Explicit registries must also pick up newly installed local TTS packages.
    (tmp_path / 'catalog.json').write_text('[]')
    package(tmp_path / 'ema-lightning')
    pocket = package(tmp_path / 'pocket-tts-turkish', 'pocket_tts_turkish')
    models = {m['id']: m for m in ai_settings.catalog()}
    assert models['ema-lightning']['available']
    assert models['pocket-tts-turkish']['available']
    assert models['pocket-tts-turkish']['languages'] == ['tr']
    (pocket.parent / 'model.safetensors').unlink()
    models = {m['id']: m for m in ai_settings.catalog()}
    assert not models['pocket-tts-turkish']['available']
    assert models['ema-lightning']['available']


def test_package_hashes_and_cache_identity_detect_changes(tmp_path):
    manifest = package(tmp_path)
    assert tts_backends.package_manifest(manifest, verify=True)['backend'] == 'ema_lightning'
    before = tts_backends.cache_identity(manifest)
    (tmp_path / 'ema.pt').write_bytes(b'corrupted model')
    assert before != tts_backends.cache_identity(manifest)
    with pytest.raises(ValueError, match='doğrulanamadı'):
        tts_backends.package_manifest(manifest, verify=True)


def test_streaming_pcm_supports_multiple_requests_and_cancellation(fake_runtime):
    voice = load_voice({'tts': str(fake_runtime), 'tts_threads': 1})
    try:
        first_process = voice.process
        for _ in range(2):
            chunks = list(voice.synthesize('Merhaba.'))
            assert [c.sample_rate for c in chunks] == [24000, 24000]
            assert [c.audio_int16_bytes for c in chunks] == [b'\x0a\x00' * 2000, b'\x02\x00' * 4000]
        pending = voice.synthesize('İptal.')
        next(pending)
        pending.close()
        assert first_process.poll() is not None
        assert voice.process is None
        assert len(list(voice.synthesize('Yeni konuşma.'))) == 2
    finally:
        voice.close()


def test_failed_synthesis_closes_model_process(fake_runtime):
    voice = load_voice({'tts': str(fake_runtime), 'tts_threads': 1})
    with pytest.raises(RuntimeError, match='model failure'):
        list(voice.synthesize('error'))
    assert voice.process is None


def test_buffered_voice_finishes_generation_before_speaker_consumption(fake_runtime):
    document = json.loads(fake_runtime.read_text())
    document['playback_mode'] = 'buffered'
    fake_runtime.write_text(json.dumps(document))
    voice = load_voice({'tts': str(fake_runtime), 'tts_threads': 1})
    try:
        with pytest.raises(RuntimeError, match='generation failed after first chunk'):
            next(voice.synthesize('late_error'))
        chunks = voice.synthesize('Tamponlu konuşma.')
        first = next(chunks)
        # The model's second chunk and done marker were already consumed.
        # A slow model cannot leave ALSA waiting between these audio pieces.
        assert first.audio_int16_bytes == b'\x0a\x00' * 2000
        assert len(list(chunks)) == 1
        assert len(list(voice.synthesize('Sonraki cümle.'))) == 2
    finally:
        voice.close()


def test_activation_cues_accept_new_model_manifests(fake_runtime, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    config = dict(tts=str(fake_runtime), tts_threads=1, language='tr',
                  greeting_text='Merhaba.', farewell_text='')
    first = cached_cues(config)
    assert Path(first['greeting']).is_file()
    assert cached_cues(config) == first
