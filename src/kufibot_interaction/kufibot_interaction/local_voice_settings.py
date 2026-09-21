"""Workflow-local voice options; provider/model settings keep their existing schema."""
import math

# UI and backend share the same bounds. Missing values inherit the robot config.
VOICE_FIELDS = {
    'max_session_sec': dict(label='Azami görüşme süresi (saniye)', default=0, min=0, max=14400, step=1,
                            help='0: süre sınırı yok. Süre, modeller hazır olup kayıt başladığında işler.'),
    'max_utterance_sec': dict(label='Tek konuşmanın azami süresi (saniye)', default=30, min=1, max=120, step=1,
                             help='Kullanıcının tek seferde konuşabileceği en uzun süre.'),
    'vad_threshold': dict(label='Konuşma algılama eşiği', default=.5, min=.01, max=.99, step=.01,
                         help='Yüksek değer daha güçlü konuşma ister; düşük değer sessiz konuşmayı daha kolay algılar.'),
    'vad_silence_ms': dict(label='Konuşma sonu sessizliği (ms)', default=600, min=32, max=5000, step=1,
                          help='Bu kadar sessizlikten sonra kullanıcının konuşması tamamlanır.'),
    'vad_min_speech_ms': dict(label='Asgari konuşma süresi (ms)', default=96, min=32, max=1000, step=1,
                             help='Kısa gürültülerin konuşma sayılmasını önler.'),
    'vad_pre_roll_ms': dict(label='VAD ön tamponu (ms)', default=300, min=32, max=2000, step=1,
                           help='VAD konuşmayı algıladığında önceki sesi de STT’ye gönderir. '
                                'İlk kelimeleri korumaya yardımcı olur. Varsayılan: 300 ms; aralık: 32–2000 ms. '
                                'Boş bırakırsanız robotun ayarı kullanılır.'),
}
AEC_MODES = ('system', 'enabled', 'disabled')


def validate_voice_options(value):
    if not isinstance(value, dict) or set(value) - {*VOICE_FIELDS, 'aec_mode'}:
        raise ValueError('Geçersiz yerel ses ayarları')
    for key, spec in VOICE_FIELDS.items():
        if key not in value:
            continue
        number = value[key]
        if (isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
                or not spec['min'] <= number <= spec['max']):
            raise ValueError(f"{spec['label']}: {spec['min']}–{spec['max']} arasında olmalı")
        if spec['step'] == 1 and number != int(number):
            raise ValueError(f"{spec['label']}: tam sayı olmalı")
    if value.get('aec_mode', 'system') not in AEC_MODES:
        raise ValueError('Geçersiz AEC seçimi')
    if value.get('vad_min_speech_ms', 96) > value.get('max_utterance_sec', 30) * 1000:
        raise ValueError('Asgari konuşma süresi, tek konuşma sınırını aşamaz')
    return dict(value)


def workflow_voice_options(document):
    settings = (document or {}).get('settings', {})
    if not isinstance(settings, dict):
        raise ValueError('Workflow ayarları nesne olmalı')
    return validate_voice_options(settings.get('voice', {}))


def apply_voice_options(config, document):
    return {**config, **workflow_voice_options(document)}
