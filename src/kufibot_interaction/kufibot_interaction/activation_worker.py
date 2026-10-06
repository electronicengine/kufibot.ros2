"""Isolated wake STT / cached Piper cue worker. One JSON config on stdin."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import wave

from .voice_activation import matches, tokens


def cached_cues(config):
    from .local_voice_worker import load_voice
    root = Path(os.environ.get('XDG_CACHE_HOME', '~/.cache')).expanduser() / 'kufibot/voice-cues'
    root.mkdir(parents=True, exist_ok=True)
    voice = None
    result = {}
    for name in ('greeting', 'farewell'):
        text = config[name + '_text'].strip()
        if not text:
            continue
        model = Path(config['tts'])
        key = json.dumps([text, config['language'], str(model), model.stat().st_mtime_ns,
                          Path(str(model) + '.json').stat().st_mtime_ns])
        path = root / (hashlib.sha256(key.encode()).hexdigest() + '.wav')
        if not path.exists():
            voice = voice or load_voice(config)
            fd, temporary = tempfile.mkstemp(dir=root, suffix='.wav')
            os.close(fd)
            try:
                with wave.open(temporary, 'wb') as output:
                    output.setsampwidth(2)
                    first = True
                    for chunk in voice.synthesize(text):
                        if first:
                            output.setnchannels(chunk.sample_channels)
                            output.setframerate(chunk.sample_rate)
                            first = False
                        output.writeframes(chunk.audio_int16_bytes)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        result[name] = str(path)
    return result


def report_heard(text, config, notify):
    matched = matches(text, config['phrase'], config['language'])
    if text.strip():
        notify('heard', text=text, matched=matched)
    return matched


def main():
    from .local_voice_worker import create_stt, listen_vad, emit
    from .local_voice_runtime import SileroVad, Reporter
    from .audio_devices import audio_command
    # Parent terminates the whole process group, including capture/player.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    config = json.loads(sys.stdin.readline())
    try:
        if config['operation'] == 'prepare':
            emit('prepared', paths=cached_cues(config))
        elif config['operation'] == 'play':
            with wave.open(config['path'], 'rb') as audio:
                subprocess.run(audio_command(False, config['speaker'], audio.getframerate(), audio.getnchannels()),
                               input=audio.readframes(audio.getnframes()), check=True, timeout=120)
        else:
            config['wake_diagnostics'] = True
            emit('diagnostic', message=f'Wake listener: phrase={config["phrase"]!r}, '
                 f'language={config["language"]}, min_speech_ms={config["vad_min_speech_ms"]}, '
                 f'threshold={config["vad_threshold"]}')
            backend = create_stt(config)
            if hasattr(backend, 'model'):
                missing = [word for word in tokens(config['phrase'], config['language'])
                           if backend.model.vosk_model_find_word(word) < 0]
                if missing:
                    backend.close()
                    if config.get('fallback_stt'):
                        del backend
                        config.update(stt=config['fallback_stt'], stt_backend='hailo_whisper')
                        emit('diagnostic', message='Uyanma ifadesi Vosk sözlüğünde yok; otomatik Whisper seçildi')
                        backend = create_stt(config)
                    else:
                        raise ValueError('Uyanma ifadesi Vosk sözlüğünde yok: ' + ', '.join(missing) +
                                         '. Whisper STT seçin veya farklı bir ifade kullanın.')
            reporter = Reporter(lambda *args, **kwargs: None)
            try:
                vad = SileroVad(config['vad_model'])
                while True:
                    text = listen_vad(config, backend, vad, reporter)
                    matched = report_heard(text, config, emit)
                    emit('diagnostic', message=f'Wake match: phrase={config["phrase"]!r}, matched={matched}')
                    if matched:
                        emit('wake')
                        break
            finally:
                reporter.close()
                backend.close()
    except Exception as exc:
        emit('error', message=str(exc))
        sys.exit(1)


if __name__ == '__main__':
    main()
