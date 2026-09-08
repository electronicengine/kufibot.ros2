"""Robot-owned local model registry and persistent voice configuration."""
import json
import os
from pathlib import Path
import tempfile

DEFAULT_SYSTEM_PROMPT = (
    'You are Kufibot, a friendly voice assistant. '
    'Reply in the selected language. Keep replies brief, at most three sentences. '
    'You cannot see the camera or control robot hardware in this local session.')
DEFAULT = dict(provider='verasist', language='tr', stt='', llm='', tts='',
               system_prompt=DEFAULT_SYSTEM_PROMPT)
KINDS = ('stt', 'llm', 'tts')


def catalog_path():
    return Path(os.environ.get('KUFIBOT_AI_CATALOG', '/usr/local/ai.models/catalog.json'))


def settings_path():
    return Path(os.environ.get('KUFIBOT_AI_SETTINGS', '~/.config/kufibot/ai.json')).expanduser()


def discover_models(root):
    """Recognize the existing Kufibot model layout; custom models use catalog.json."""
    entries = []
    for folder, language in [('trRecognizeModel', 'tr'), ('engRecognizeModel', 'en')]:
        if (root / folder).is_dir():
            entries.append(dict(id=folder, kind='stt', languages=[language], path=str(root / folder)))
    for folder in ('trSpeechModel', 'engSpeechModel'):
        for model in sorted((root / folder).glob('*.onnx')):
            try:
                metadata = json.loads(Path(str(model) + '.json').read_text())
                language = metadata['language']['code'].split('_')[0]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            entries.append(dict(id=model.stem, kind='tts', languages=[language], path=str(model)))
    for name, languages in [('turkish_ytu', ['tr']), ('dolphin3', ['tr', 'en'])]:
        model = root / 'llamaModel' / (name + '.gguf')
        if model.is_file():
            entries.append(dict(id=name, kind='llm', languages=languages, path=str(model)))
    return entries


def catalog():
    path = catalog_path()
    entries = (json.loads(path.read_text()) if path.exists() else
               discover_models(Path(os.environ.get('KUFIBOT_AI_MODEL_ROOT', '/usr/local/ai.models'))))
    if not isinstance(entries, list):
        raise ValueError('Model kataloğu bir liste olmalı')
    result = []
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get('kind') not in KINDS
                or not isinstance(entry.get('id'), str) or not entry['id']
                or entry['id'] in seen
                or not isinstance(entry.get('languages'), list)
                or not entry['languages']
                or not all(isinstance(v, str) and v for v in entry['languages'])
                or not isinstance(entry.get('path'), str)):
            raise ValueError('Geçersiz model kataloğu kaydı')
        seen.add(entry['id'])
        model = Path(entry['path']).expanduser()
        if not model.is_absolute():
            model = path.parent / model
        available = ((model / 'am/final.mdl').is_file() or (model / 'final.mdl').is_file() if entry['kind'] == 'stt'
                     else model.is_file())
        if entry['kind'] == 'tts':
            available = available and Path(str(model) + '.json').is_file()
        result.append({**entry, 'path': str(model), 'available': available})
    return result


def validate(value, models=None):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Sağlayıcı, dil, STT, LLM ve TTS seçimi gerekli')
    if not all(isinstance(v, str) for v in value.values()):
        raise ValueError('Ayar değerleri metin olmalı')
    if value['provider'] not in ('verasist', 'local'):
        raise ValueError('Geçersiz AI sağlayıcısı')
    if not value['language'] or len(value['language']) > 32:
        raise ValueError('Geçersiz dil')
    if len(value['system_prompt'].strip()) > 6000:
        raise ValueError('Sistem mesajı en fazla 6000 karakter olabilir')
    if value['provider'] == 'local':
        models = catalog() if models is None else models
        for kind in KINDS:
            match = next((m for m in models if m['id'] == value[kind] and m['kind'] == kind), None)
            if not match or not match['available']:
                raise ValueError(f'{kind.upper()} modeli robotta kurulu değil')
            if value['language'] not in match['languages']:
                raise ValueError(f'{kind.upper()} modeli seçilen dili desteklemiyor')
    return dict(value)


def read_settings():
    path = settings_path()
    if not path.exists():
        return dict(DEFAULT)
    value = json.loads(path.read_text())
    # Upgrade selections written before the editable local system prompt.
    if isinstance(value, dict) and set(value) == set(DEFAULT) - {'system_prompt'}:
        value = {**value, 'system_prompt': DEFAULT_SYSTEM_PROMPT}
    # Keep selections visible even if a model disk is temporarily unavailable.
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Geçersiz kayıtlı AI ayarları')
    if value['provider'] not in ('local', 'verasist') or not all(isinstance(v, str) for v in value.values()):
        raise ValueError('Geçersiz kayıtlı AI ayarları')
    return value


def save_settings(value):
    value = validate(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.ai-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
