"""Provider-independent activation configuration and text matching (no ROS)."""
import re
import unicodedata

DEFAULT_ACTIVATION = dict(enabled=True, phrase='Kufi', language='tr', stt='', tts='',
                          greeting_text='Evet, seni dinliyorum.', greeting_mimic='',
                          farewell_text='Görüşmek üzere.', farewell_mimic='')


def tokens(text, language='tr'):
    text = unicodedata.normalize('NFKC', text)
    if language.split('-')[0] == 'tr':
        text = text.replace('I', 'ı').replace('İ', 'i')
    return re.findall(r'[^\W_]+', text.casefold(), re.UNICODE)


def matches(text, phrase, language='tr'):
    words, wanted = tokens(text, language), tokens(phrase, language)
    return bool(wanted) and any(words[i:i + len(wanted)] == wanted
                               for i in range(len(words) - len(wanted) + 1))


def validate_activation(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_ACTIVATION):
        raise ValueError('Geçersiz uyanma ayarları')
    result = {**DEFAULT_ACTIVATION, **value}
    if type(result['enabled']) is not bool:
        raise ValueError('Uyanma dinlemesi boolean olmalı')
    for key in DEFAULT_ACTIVATION:
        if key != 'enabled' and not isinstance(result[key], str):
            raise ValueError('Uyanma ayarları metin olmalı')
    for key in ('greeting_text', 'farewell_text'):
        if len(result[key]) > 500:
            raise ValueError('Sabit söyleyiş en fazla 500 karakter olabilir')
    if not result['language'] or len(result['language']) > 32:
        raise ValueError('Geçersiz dinleyici dili')
    language = result['language'].strip().lower().replace('_', '-').split('-')[0]
    result['language'] = {'eng': 'en', 'tur': 'tr'}.get(language, language)
    if len(result['phrase']) > 120 or (result['enabled'] and not tokens(result['phrase'], result['language'])):
        raise ValueError('Uyanma kelimesi gerekli; en fazla 120 karakter olabilir')
    return result


def resolve_activation(value, models, *, listen=True):
    result = validate_activation(value)
    for kind in ('stt', 'tts'):
        needed = (listen and result['enabled']) if kind == 'stt' else bool(result['greeting_text'] or result['farewell_text'])
        if not needed:
            continue
        candidates = sorted((m for m in models if m['kind'] == kind and m['available']
                             and result['language'] in m['languages']),
                            key=lambda m: (m.get('backend', 'vosk') != 'vosk', m['id']))
        model = next((m for m in candidates if m['id'] == result[kind]), None) if result[kind] else next(iter(candidates), None)
        if model is None:
            raise ValueError(f'Uyanma için {result["language"]} {kind.upper()} modeli robotta kurulu değil; Sesli Ajan ayarlarından seçin')
        result[kind] = model['path']
        if kind == 'stt':
            result['stt_backend'] = model.get('backend', 'vosk')
            if not value.get('stt'):
                alternate = next((m for m in candidates if m.get('backend') == 'hailo_whisper'), None)
                if alternate:
                    result['fallback_stt'] = alternate['path']
    return result
