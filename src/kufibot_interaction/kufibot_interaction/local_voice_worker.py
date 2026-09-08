"""Isolated offline Vosk -> llama.cpp -> Piper worker; stdout is JSONL."""
import json
import os
import select
import time
import subprocess
import sys
import tempfile
import wave


def emit(kind, **values):
    print(json.dumps(dict(type=kind, **values)), flush=True)


def listen(config, recognizer, notify=emit):
    """Read one turn; expected termination must not look like an ALSA failure."""
    import numpy as np

    # A file avoids a stderr pipe filling up while PCM is being consumed.
    with tempfile.TemporaryFile() as errors:
        mic = subprocess.Popen(
            ['arecord', '-q', '-D', config['mic'], '-f', 'S16_LE',
             '-r', '16000', '-c', '1', '-t', 'raw',
             '--buffer-time=200000', '--period-time=20000'],
            stdout=subprocess.PIPE, stderr=errors, bufsize=0)
        try:
            recognizer.Reset()
            received = 0
            announced = False
            next_diagnostic = time.monotonic() + 5
            buffered = b''
            while True:
                if not select.select([mic.stdout], [], [], 5)[0]:
                    raise RuntimeError(f'Mikrofon {config["mic"]}: 5 saniyedir PCM verisi gelmiyor')
                chunk = os.read(mic.stdout.fileno(), 4000)
                if not chunk:
                    errors.seek(0)
                    detail = errors.read().decode(errors='replace').strip()
                    raise RuntimeError(f'Mikrofon {config["mic"]}: kayıt durdu. {detail}')
                buffered += chunk
                if len(buffered) < 4000:
                    continue
                pcm, buffered = buffered[:4000], buffered[4000:]
                if not announced:
                    notify('ready')
                    announced = True
                received += len(pcm)
                rms = float(np.sqrt(np.mean(np.frombuffer(pcm, dtype=np.int16).astype(float) ** 2)))
                if recognizer.AcceptWaveform(pcm):
                    text = json.loads(recognizer.Result()).get('text', '').strip()
                    if text:
                        return text
                if received >= 16000 * 2 * 30:
                    text = json.loads(recognizer.FinalResult()).get('text', '').strip()
                    received = 0
                    if text:
                        return text
                if time.monotonic() >= next_diagnostic:
                    partial = json.loads(recognizer.PartialResult()).get('partial', '')
                    notify('diagnostic', message=f'Microphone {config["mic"]}: PCM OK, RMS={rms:.0f}, partial={partial!r}')
                    next_diagnostic = time.monotonic() + 5
        finally:
            # arecord reports EINTR when terminated during pcm_read. This is
            # our normal half-duplex turn boundary, not a capture failure.
            if mic.poll() is None:
                mic.terminate()
            try:
                mic.wait(timeout=2)
            except subprocess.TimeoutExpired:
                mic.kill()
                mic.wait(timeout=2)
            mic.stdout.close()


def run(config):
    from vosk import Model, KaldiRecognizer
    from llama_cpp import Llama
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from piper import PiperVoice

    recognizer = KaldiRecognizer(Model(config['stt']), 16000)
    llm = Llama(model_path=config['llm'], n_ctx=2048,
                n_threads=max(1, min(4, os.cpu_count() or 1)), verbose=False)
    voice = PiperVoice.load(config['tts'])
    system = {'role': 'system', 'content': config['system_prompt'].strip()}
    history = []
    while True:
        text = listen(config, recognizer)
        emit('transcript', role='user', text=text)
        # The main ROS node uses this only for Local AI to release CPU from
        # camera capture, video encoding, and MediaPipe while llama.cpp runs.
        emit('compute', active=True)
        emit('state', state='thinking')
        # A bounded history and user input keep the small RPi context predictable.
        messages = [system] + history[-4:] + [{'role': 'user', 'content': text[:1500]}]
        while len(llm.tokenize(json.dumps(messages).encode())) > 1400 and len(messages) > 2:
            del messages[1:3]
        response = llm.create_chat_completion(messages=messages, max_tokens=160, temperature=0.7)
        answer = response['choices'][0]['message']['content'] or ''
        history = (history + [{'role': 'user', 'content': text[:1500]},
                              {'role': 'assistant', 'content': answer}])[-4:]
        emit('compute', active=False)
        emit('transcript', role='assistant', text=answer)
        with tempfile.TemporaryDirectory(prefix='kufibot-voice-') as folder:
            path = os.path.join(folder, 'reply.wav')
            with wave.open(path, 'wb') as stream:
                voice.synthesize_wav(answer, stream)
            emit('speaking', value=True)
            subprocess.run(['aplay', '-q', '-D', config['speaker'], path], check=True)
            emit('speaking', value=False)
        emit('state', state='connecting', detail='Mikrofon yeniden açılıyor')


if __name__ == '__main__':
    try:
        run(json.loads(sys.stdin.readline()))
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)
