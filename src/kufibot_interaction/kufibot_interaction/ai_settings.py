"""Robot-owned local model registry and persistent voice configuration."""
import json
import os
from pathlib import Path
import tempfile

DEFAULT_SYSTEM_PROMPT = (
    'You are Kufibot, a friendly voice assistant. '
    'Reply in the selected language. Keep replies brief, at most three sentences. '
    'You cannot see the camera or control robot hardware in this local session.')
DEFAULT = dict(provider='verasist', language='tr', stt='', llm='', embedding='', tts='',
               system_prompt=DEFAULT_SYSTEM_PROMPT, camera_attach_to_every_user_turn=False, workflow_id='')
KINDS = ('stt', 'llm', 'embedding', 'tts')


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
    # GGUF files are usable as both the local LLM and the expression embedding
    # model.  Keep the LLM id stable for saved settings and namespace the
    # embedding id so a single catalog can expose both roles safely.
    for model in sorted((root / 'llamaModel').glob('*.gguf')):
        entries.append(dict(id=model.stem, kind='llm', languages=['tr', 'en'], path=str(model)))
        entries.append(dict(id=f'embedding:{model.stem}', kind='embedding', languages=['tr', 'en'],
                            path=str(model), label=f'{model.stem} · embedding'))
    for variant in ('tiny', 'base'):
        folder = root / f'whisper-{variant}-hailo8l'
        if (folder / 'manifest.json').is_file():
            entries.append(dict(id=folder.name, kind='stt', languages=['tr', 'en'],
                                backend='hailo_whisper', path=str(folder),
                                label=f'Whisper {variant} · Hailo-8L'))
    return entries


def catalog():
    path = catalog_path()
    discovered = discover_models(Path(os.environ.get('KUFIBOT_AI_MODEL_ROOT', '/usr/local/ai.models')))
    custom_catalog = path.exists()
    entries = json.loads(path.read_text()) if custom_catalog else discovered
    if not isinstance(entries, list):
        raise ValueError('Model kataloğu bir liste olmalı')
    # The optional catalog supplements hardware-specific STT/TTS declarations;
    # it must not hide GGUF files copied into llamaModel by an operator.
    declared = {entry.get('id') for entry in entries if isinstance(entry, dict)}
    additions = (entry for entry in discovered if entry['id'] not in declared and
                 (not custom_catalog or entry['kind'] in ('llm', 'embedding')))
    entries = [*entries, *additions]
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
        if entry['kind'] == 'stt':
            backend = entry.get('backend', 'vosk')
            if backend not in ('vosk', 'hailo_whisper'):
                raise ValueError('Unsupported STT backend')
            entry = {**entry, 'backend': backend}
            if backend == 'hailo_whisper':
                from .hailo_whisper import package_manifest
                try:
                    manifest = package_manifest(model)
                    available = all(lang in manifest['languages'] for lang in entry['languages'])
                except (OSError, ValueError, KeyError, TypeError):
                    available = False
        if entry['kind'] == 'tts':
            available = available and Path(str(model) + '.json').is_file()
        result.append({**entry, 'path': str(model), 'available': available})
    return result


def normalize(value):
    """Upgrade older clients/files without accepting unknown settings."""
    if isinstance(value, dict):
        value = dict(value)
        value.setdefault('system_prompt', DEFAULT_SYSTEM_PROMPT)
        value.setdefault('camera_attach_to_every_user_turn', False)
        value.setdefault('embedding', '')
        value.setdefault('workflow_id', '')
    return value


def validate(value, models=None):
    value = normalize(value)
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Sağlayıcı, dil, STT, LLM, embedding ve TTS seçimi gerekli')
    if type(value['camera_attach_to_every_user_turn']) is not bool:
        raise ValueError('Kamera seçeneği boolean olmalı')
    if not all(isinstance(value[k], str) for k in DEFAULT
               if k != 'camera_attach_to_every_user_turn'):
        raise ValueError('Diğer ayar değerleri metin olmalı')
    if value['provider'] not in ('verasist', 'local'):
        raise ValueError('Geçersiz AI sağlayıcısı')
    if not value['language'] or len(value['language']) > 32:
        raise ValueError('Geçersiz dil')
    if len(value['system_prompt'].strip()) > 6000:
        raise ValueError('Sistem mesajı en fazla 6000 karakter olabilir')
    if value['provider'] == 'local' and value.get('workflow_id'):
        from .workflows import WorkflowStore
        document = WorkflowStore().snapshot(value['workflow_id'])
        resolved = validate({**value, **document.get('settings', {}), 'provider': 'local', 'workflow_id': ''}, models)
        return {**resolved, 'workflow_id': value['workflow_id']}
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
    value = normalize(value)
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Geçersiz kayıtlı AI ayarları')
    if (value['provider'] not in ('local', 'verasist')
            or type(value['camera_attach_to_every_user_turn']) is not bool
            or not all(isinstance(value[k], str) for k in DEFAULT
                       if k != 'camera_attach_to_every_user_turn')):
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
