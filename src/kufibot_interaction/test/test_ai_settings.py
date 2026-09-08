import json

import pytest

from kufibot_interaction import ai_settings as ai
from kufibot_remote.control import Control


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_AI_CATALOG', str(tmp_path / 'catalog.json'))
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(tmp_path / 'settings.json'))
    stt = tmp_path / 'vosk' / 'am'
    stt.mkdir(parents=True)
    (stt / 'final.mdl').touch()
    (tmp_path / 'chat.gguf').touch()
    (tmp_path / 'voice.onnx').touch()
    (tmp_path / 'voice.onnx.json').write_text('{}')
    entries = [dict(id=kind, kind=kind, languages=['tr'], path=path)
               for kind, path in [('stt', 'vosk'), ('llm', 'chat.gguf'), ('tts', 'voice.onnx')]]
    (tmp_path / 'catalog.json').write_text(json.dumps(entries))
    return dict(provider='local', language='tr', stt='stt', llm='llm', tts='tts',
                system_prompt='Kısa cevap ver.')


def test_settings_persist_and_resolve_relative_models(registry):
    ai.save_settings(registry)
    assert ai.read_settings() == registry
    assert all(m['available'] for m in ai.catalog())


@pytest.mark.parametrize('change', [dict(language='en'), dict(stt='/tmp/evil'),
                                     dict(provider='unknown'), dict(llm=None)])
def test_invalid_selections_do_not_replace_saved_settings(registry, change):
    ai.save_settings(registry)
    with pytest.raises(ValueError):
        ai.save_settings({**registry, **change})
    assert ai.read_settings() == registry


def test_missing_piper_sidecar_rejected(registry):
    ai.catalog_path().with_name('voice.onnx.json').unlink()
    with pytest.raises(ValueError, match='TTS'):
        ai.validate(registry)


def test_cloud_does_not_require_installed_models(registry):
    ai.save_settings({**ai.DEFAULT, 'stt': 'missing'})
    assert ai.read_settings()['provider'] == 'verasist'


def test_old_settings_gain_the_default_system_prompt(registry):
    old = {key: value for key, value in registry.items() if key != 'system_prompt'}
    ai.settings_path().write_text(json.dumps(old))
    assert ai.read_settings()['system_prompt'] == ai.DEFAULT_SYSTEM_PROMPT


def test_system_prompt_is_persisted_and_limited(registry):
    prompt = 'Sadece Türkçe ve kısa cevap ver.'
    ai.save_settings({**registry, 'system_prompt': prompt})
    assert ai.read_settings()['system_prompt'] == prompt
    with pytest.raises(ValueError, match='6000'):
        ai.validate({**registry, 'system_prompt': 'x' * 6001})


def test_settings_command_requires_owner_and_valid_models(registry):
    control = Control()
    owner = object()
    command = dict(type='setAiSettings', settings=registry)
    with pytest.raises(ValueError):
        control.command(owner, command)
    control.command(owner, dict(type='claim'))
    control.command(owner, command)
    assert control.ai_settings_requested == registry
    assert control.mode == 'remote'


def test_existing_robot_layout_detected(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_AI_CATALOG', str(tmp_path / 'absent.json'))
    monkeypatch.setenv('KUFIBOT_AI_MODEL_ROOT', str(tmp_path))
    (tmp_path / 'trRecognizeModel').mkdir()
    (tmp_path / 'trRecognizeModel/final.mdl').touch()
    assert ai.catalog()[0]['available']
    assert ai.catalog()[0]['languages'] == ['tr']
