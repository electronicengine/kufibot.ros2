"""Robot-owned local model registry and persistent voice configuration."""
import json
import os
from pathlib import Path
import tempfile
from copy import deepcopy
from .voice_activation import DEFAULT_ACTIVATION, validate_activation

DEFAULT_SYSTEM_PROMPT = ''
DEFAULT = dict(provider='verasist', language='tr', stt='', llm='', embedding='', tts='',
               system_prompt=DEFAULT_SYSTEM_PROMPT, camera_attach_to_every_user_turn=False, workflow_id='',
               activation=DEFAULT_ACTIVATION, verasist_audio={})
KINDS = ('stt', 'llm', 'embedding', 'tts')
VERASIST_AUDIO_FIELDS = {
    'aec_mode', 'mute_mic_during_playback', 'noise_gate_rms',
    'noise_gate_hangover_sec', 'noise_suppression_db', 'barge_in_rms',
    'barge_in_start_sec', 'playback_echo_tail_sec', 'aec_play_delay_ms',
    'aec_high_pass_filter', 'aec_noise_suppression', 'aec_gain_control',
    'aec_extended_filter', 'aec_delay_agnostic',
}


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
        value.setdefault('activation', deepcopy(DEFAULT_ACTIVATION))
        value.setdefault('verasist_audio', {})
    return value


def workflow_voice_settings(document):
    """Keep workflow routing settings separate from the voice provider schema."""
    value = dict(document.get('settings', {}))
    llm_enabled = value.pop('llm_enabled', True)
    if type(llm_enabled) is not bool:
        raise ValueError('LLM kullanımı boolean olmalı')
    value.pop('semantic_threshold', None)
    value.pop('voice', None)
    value.pop('activation', None)
    # Workflow prompts live exclusively on nodes. Ignore old shared prompts.
    value['system_prompt'] = ''
    if not llm_enabled:
        value['llm'] = ''
        value['embedding'] = ''
    return value


def validate(value, models=None, *, workflow_llm_enabled=True):
    value = normalize(value)
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Sağlayıcı, dil, STT, LLM, embedding ve TTS seçimi gerekli')
    if type(value['camera_attach_to_every_user_turn']) is not bool:
        raise ValueError('Kamera seçeneği boolean olmalı')
    audio = value['verasist_audio']
    if not isinstance(audio, dict) or set(audio) - VERASIST_AUDIO_FIELDS:
        raise ValueError('Geçersiz Verasist ses ayarları')
    if audio.get('aec_mode', 'system') not in ('system', 'enabled', 'disabled'):
        raise ValueError('Geçersiz Verasist AEC seçimi')
    if ('mute_mic_during_playback' in audio
            and type(audio['mute_mic_during_playback']) is not bool):
        raise ValueError('Mikrofon sessize alma seçeneği boolean olmalı')
    for key in ('aec_high_pass_filter', 'aec_noise_suppression',
                'aec_gain_control', 'aec_extended_filter', 'aec_delay_agnostic'):
        if key in audio and type(audio[key]) is not bool:
            raise ValueError(f'Geçersiz Verasist AEC ayarı: {key}')
    limits = {
        'noise_gate_rms': (0, 32768), 'noise_gate_hangover_sec': (0, 5),
        'noise_suppression_db': (-60, 0), 'barge_in_rms': (0, 32768),
        'barge_in_start_sec': (.01, .5), 'playback_echo_tail_sec': (0, 5),
        'aec_play_delay_ms': (0, 500),
    }
    for key, (minimum, maximum) in limits.items():
        if key in audio and (isinstance(audio[key], bool)
                             or not isinstance(audio[key], (int, float))
                             or not minimum <= audio[key] <= maximum):
            raise ValueError(f'Geçersiz Verasist ses ayarı: {key}')
    if not all(isinstance(value[k], str) for k in DEFAULT
               if k not in ('camera_attach_to_every_user_turn', 'activation', 'verasist_audio')):
        raise ValueError('Diğer ayar değerleri metin olmalı')
    value['activation'] = validate_activation(value['activation'])
    if value['provider'] not in ('verasist', 'local'):
        raise ValueError('Geçersiz AI sağlayıcısı')
    if not value['language'] or len(value['language']) > 32:
        raise ValueError('Geçersiz dil')
    if len(value['system_prompt'].strip()) > 6000:
        raise ValueError('Sistem mesajı en fazla 6000 karakter olabilir')
    if value['provider'] == 'local' and value.get('workflow_id'):
        from .workflows import WorkflowStore
        document = WorkflowStore().snapshot(value['workflow_id'])
        llm_enabled = document.get('settings', {}).get('llm_enabled', True)
        resolved = validate({**value, **workflow_voice_settings(document), 'provider': 'local', 'workflow_id': ''},
                           models, workflow_llm_enabled=llm_enabled)
        return {**resolved, 'workflow_id': value['workflow_id']}
    if value['provider'] == 'local':
        models = catalog() if models is None else models
        required_kinds = KINDS if workflow_llm_enabled else ('stt', 'tts')
        for kind in required_kinds:
            match = next((m for m in models if m['id'] == value[kind] and m['kind'] == kind), None)
            if not match or not match['available']:
                raise ValueError(f'{kind.upper()} modeli robotta kurulu değil')
            if value['language'] not in match['languages']:
                raise ValueError(f'{kind.upper()} modeli seçilen dili desteklemiyor')
    return dict(value)


def read_settings():
    path = settings_path()
    if not path.exists():
        return deepcopy(DEFAULT)
    value = json.loads(path.read_text())
    value = normalize(value)
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Geçersiz kayıtlı AI ayarları')
    if (value['provider'] not in ('local', 'verasist')
            or type(value['camera_attach_to_every_user_turn']) is not bool
            or not isinstance(value.get('verasist_audio'), dict)
            or not all(isinstance(value[k], str) for k in DEFAULT
                       if k not in ('camera_attach_to_every_user_turn', 'activation', 'verasist_audio'))):
        raise ValueError('Geçersiz kayıtlı AI ayarları')
    value['activation'] = validate_activation(value['activation'])
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
