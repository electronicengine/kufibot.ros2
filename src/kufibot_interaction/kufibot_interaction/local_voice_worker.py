"""Isolated offline VAD/STT -> llama.cpp -> streaming Piper worker; JSONL events."""
import json
import os
import select
import time
import subprocess
import sys
import tempfile
import multiprocessing
import queue
import threading
from pathlib import Path

from .local_voice_runtime import Reporter, SileroVad, SpeechGate, validate_runtime
from .sentence_stream import SentenceBuffer


_output_lock = multiprocessing.get_context('fork').RLock()


def emit(kind, **values):
    with _output_lock:
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



class VoskBackend:
    def __init__(self, path):
        from vosk import Model, KaldiRecognizer
        self.recognizer = KaldiRecognizer(Model(path), 16000)
        self.reset()

    def reset(self):
        self.recognizer.Reset()
        self.parts = []

    def feed(self, pcm):
        if self.recognizer.AcceptWaveform(pcm):
            self.parts.append(json.loads(self.recognizer.Result()).get('text', ''))

    def finish(self, trailing_silence_ms=0):
        self.parts.append(json.loads(self.recognizer.FinalResult()).get('text', ''))
        return ' '.join(part for part in self.parts if part).strip()

    def partial(self):
        current = json.loads(self.recognizer.PartialResult()).get('partial', '')
        return ' '.join(part for part in [*self.parts, current] if part).strip()

    def close(self):
        pass


def create_stt(config):
    if config.get('stt_backend', 'vosk') == 'vosk':
        return VoskBackend(config['stt'])
    if config['stt_backend'] == 'hailo_whisper':
        from .hailo_whisper import HailoWhisper
        return HailoWhisper(config['stt'], config['language'], config['hailo_timeout_sec'])
    raise ValueError('Unsupported STT backend')


def listen_vad(config, backend, vad, reporter, recorder=None):
    """Endpoint with VAD, release capture before batch STT (Hailo) starts."""
    import numpy as np
    backend.reset()
    vad.reset()
    gate = SpeechGate(config)
    reporter.set_phase('listening')
    with tempfile.TemporaryFile() as errors:
        mic = subprocess.Popen(
            ['arecord', '-q', '-D', config['mic'], '-f', 'S16_LE', '-r', '16000',
             '-c', '1', '-t', 'raw', '--buffer-time=200000', '--period-time=20000'],
            stdout=subprocess.PIPE, stderr=errors, bufsize=0)
        try:
            buffered = b''
            ready = False
            diagnostic_at = time.monotonic() + 5
            partial_at, last_partial = 0.0, ''
            sample_count, energy, clipped, peak = 0, 0.0, 0, 0.0
            while True:
                if not select.select([mic.stdout], [], [], 5)[0]:
                    raise RuntimeError(f'Microphone {config["mic"]}: no PCM for 5 seconds')
                chunk = os.read(mic.stdout.fileno(), 1024)
                if not chunk:
                    errors.seek(0)
                    raise RuntimeError(f'Microphone {config["mic"]}: {errors.read().decode(errors="replace")}')
                buffered += chunk
                if len(buffered) < 1024:
                    continue
                pcm, buffered = buffered[:1024], buffered[1024:]
                if not ready:
                    emit('ready')
                    reporter.mark('capture_ready')
                    ready = True
                probability = vad(pcm)
                frames, done = gate.push(pcm, probability, time.monotonic())
                for frame in frames:
                    if recorder:
                        recorder.write('user', frame)
                    samples = np.frombuffer(frame, dtype='<i2').astype(np.float32) / 32768.0
                    sample_count += samples.size
                    energy += float(np.dot(samples, samples))
                    clipped += int(np.count_nonzero(np.abs(samples) >= .999))
                    peak = max(peak, float(np.max(np.abs(samples))))
                    backend.feed(frame)
                if frames and hasattr(backend, 'partial') and time.monotonic() >= partial_at:
                    partial = backend.partial()
                    if partial and partial != last_partial:
                        emit('transcript', role='user', text=partial, final=False)
                        last_partial = partial
                    partial_at = time.monotonic() + .25
                if done:
                    reporter.mark('speech_end', at=gate.last_voice)
                    reporter.mark('endpoint')
                    break
                if time.monotonic() >= diagnostic_at:
                    emit('diagnostic', message=f'Microphone {config["mic"]}: PCM OK, VAD={probability:.3f}')
                    diagnostic_at = time.monotonic() + 5
        finally:
            if mic.poll() is None:
                mic.terminate()
            try:
                mic.wait(timeout=2)
            except subprocess.TimeoutExpired:
                mic.kill()
                mic.wait(timeout=2)
            mic.stdout.close()
    reporter.set_phase('transcribing')
    result = backend.finish(trailing_silence_ms=gate.silent * 32)
    if last_partial and not result:
        emit('transcript', role='user', text='', final=True)
    reporter.mark('stt_final', **getattr(backend, 'timings', {}),
                  audio_seconds=sample_count / 16000,
                  rms_dbfs=round(10 * float(np.log10(max(energy / max(sample_count, 1), 1e-12))), 1),
                  peak=round(peak, 4), clipped_samples=clipped, empty=not bool(result))
    if not result:
        emit('diagnostic', message='Konuşma algılandı ancak STT metin çıkaramadı; lütfen tekrar konuşun.')
        emit('state', state='listening', detail='Söylediğinizi anlayamadım; lütfen tekrar konuşun.')
    return result


def make_formatter(llm):
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter
    template = llm.metadata.get('tokenizer.chat_template')
    if not template:
        raise ValueError('Local LLM requires a GGUF chat template')
    def token_text(token):
        return llm.detokenize([token], special=True).decode('utf-8') if token >= 0 else ''
    formatter = Jinja2ChatFormatter(template=template, eos_token=token_text(llm.token_eos()),
                                   bos_token=token_text(llm.token_bos()), stop_token_ids=[llm.token_eos()])
    # Counting and inference must use exactly the same template.
    llm.chat_handler = formatter.to_chat_handler()
    return formatter


def fit_messages(llm, formatter, system, history, text, max_tokens):
    messages = [system] + history[-4:] + [{'role': 'user', 'content': text[:1500]}]
    limit = llm.n_ctx() - max_tokens - 8
    while len(llm.tokenize(formatter(messages=messages).prompt.encode(), add_bos=False, special=True)) > limit:
        if len(messages) > 2:
            del messages[1:3]
        elif messages[-1]['content']:
            messages[-1]['content'] = messages[-1]['content'][:-64]
        else:
            raise ValueError('System prompt exceeds the selected model context; shorten it')
    return messages


def load_voice(config):
    import onnxruntime as ort
    from piper import PiperVoice, PiperConfig
    options = ort.SessionOptions()
    options.intra_op_num_threads = config['tts_threads']
    options.inter_op_num_threads = 1
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    return PiperVoice(config=PiperConfig.from_dict(json.loads(Path(config['tts'] + '.json').read_text())),
                      session=ort.InferenceSession(config['tts'], sess_options=options,
                                                   providers=['CPUExecutionProvider']))



class SpeechCancelled(Exception):
    pass


def speak_sentences(config, voice, sentences, reporter, *, concurrent=False, stopped=None, on_player=None, recorder=None):
    """One Piper owner and one ALSA stream for the entire turn."""
    player = None
    started = synthesizing = False
    def check_cancelled():
        if stopped is not None and stopped.is_set():
            raise SpeechCancelled()
    with tempfile.TemporaryFile() as errors:
        try:
            for sentence in sentences:
                check_cancelled()
                if not sentence.strip():
                    continue
                if not synthesizing:
                    if not concurrent:
                        reporter.set_phase('synthesizing')
                    reporter.mark('tts_start')
                    synthesizing = True
                reporter.mark('tts_sentence_start')
                for chunk in voice.synthesize(sentence):
                    check_cancelled()
                    if recorder:
                        recorder.write('assistant', chunk.audio_int16_bytes, chunk.sample_rate)
                    if player is None:
                        player = subprocess.Popen(
                            ['aplay', '-q', '-D', config['speaker'], '-t', 'raw', '-f', 'S16_LE',
                             '-r', str(chunk.sample_rate), '-c', str(chunk.sample_channels)],
                            stdin=subprocess.PIPE, stderr=errors, bufsize=0)
                        if on_player:
                            on_player(player)
                    data = memoryview(chunk.audio_int16_bytes)
                    while data:
                        check_cancelled()
                        if not select.select([], [player.stdin], [], 10)[1]:
                            raise RuntimeError('Speaker write stalled for 10 seconds')
                        count = os.write(player.stdin.fileno(), data[:4096])
                        if count == 0:
                            raise RuntimeError('Speaker accepted no audio')
                        data = data[count:]
                        if not started:
                            started = True
                            reporter.mark('playback_start')
                            emit('speaking', value=True)
                reporter.mark('tts_sentence_end')
            check_cancelled()
            if synthesizing:
                reporter.mark('tts_end')
                # The sentinel arrives only after LLM completion; no premature
                # camera resume while generation or queued synthesis remains.
                reporter.set_phase('speaking')
            if player:
                player.stdin.close()
                if player.wait(timeout=120):
                    errors.seek(0)
                    raise RuntimeError('Speaker failed: ' + errors.read().decode(errors='replace'))
        finally:
            if player:
                if player.poll() is None:
                    player.terminate()
                    try:
                        player.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        player.kill()
                        player.wait(timeout=2)
                if not player.stdin.closed:
                    player.stdin.close()
            if started:
                emit('speaking', value=False)
    if started:
        reporter.mark('playback_end')


def speak(config, voice, answer, reporter):
    """Serial playback retained for fixed-text callers and comparisons."""
    speak_sentences(config, voice, [answer], reporter)


class SentenceSpeaker:
    """Bounded producer/consumer: LLM on caller, Piper on one background thread."""
    def __init__(self, config, voice, reporter, recorder=None):
        self.config, self.voice, self.reporter = config, voice, reporter
        self.pending, self.recorder = queue.Queue(maxsize=2), recorder
        self.stopped = threading.Event()
        self.error = None
        self.player = None
        self.thread = threading.Thread(target=self._run, name='local-sentence-tts', daemon=True)
        self.thread.start()

    def _set_player(self, player):
        self.player = player
        if self.stopped.is_set() and player.poll() is None:
            player.terminate()

    def _sentences(self):
        while not self.stopped.is_set():
            try:
                sentence = self.pending.get(timeout=.1)
            except queue.Empty:
                continue
            if sentence is None:
                return
            yield sentence
        raise SpeechCancelled()

    def _run(self):
        try:
            speak_sentences(self.config, self.voice, self._sentences(), self.reporter,
                            concurrent=True, stopped=self.stopped, on_player=self._set_player,
                            recorder=self.recorder)
        except SpeechCancelled:
            pass
        except Exception as error:
            self.error = error

    def check(self):
        if self.error is not None:
            raise self.error

    def put(self, sentence):
        deadline = time.monotonic() + 30
        while True:
            self.check()
            if self.stopped.is_set():
                raise SpeechCancelled()
            try:
                self.pending.put(sentence, timeout=.1)
                return
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Sentence playback queue stalled for 30 seconds')

    def finish(self):
        self.put(None)
        self.thread.join(timeout=120)
        if self.thread.is_alive():
            raise RuntimeError('Sentence playback did not finish within 120 seconds')
        self.check()

    def close(self):
        self.stopped.set()
        if self.player is not None and self.player.poll() is None:
            self.player.terminate()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError('Piper did not stop; restarting the local voice worker is required')


def generate_and_speak(config, llm, messages, voice, reporter, *, sampling=None, recorder=None):
    """Speak completed sentences while later LLM deltas are still arriving."""
    from .speech_guard import validate_speech
    reporter.set_phase('thinking')
    emit('compute', active=True)
    emit('state', state='thinking')
    speaker = SentenceSpeaker(config, voice, reporter, recorder)
    parts, accepted, buffer = [], [], SentenceBuffer()
    try:
        try:
            reporter.mark('llm_start')
            stream = llm.create_chat_completion(messages=messages, max_tokens=config['llm_max_tokens'],
                                                stream=True, **(sampling or {'temperature': .7}))
            try:
                for response in stream:
                    speaker.check()
                    content = response['choices'][0]['delta'].get('content') or ''
                    if not content:
                        continue
                    if not parts:
                        reporter.mark('llm_first_token')
                    parts.append(content)
                    for sentence in buffer.push(content):
                        validate_speech(sentence, messages)
                        accepted.append(sentence)
                        emit('transcript', role='assistant', text=' '.join(accepted), final=False)
                        reporter.mark('sentence_ready')
                        speaker.put(sentence)
            finally:
                close = getattr(stream, 'close', None)
                if close:
                    close()
            answer = ''.join(parts)
            reporter.mark('llm_end', output_tokens=len(llm.tokenize(answer.encode(), add_bos=False)))
        finally:
            emit('compute', active=False)
        reporter.set_phase('synthesizing')
        for sentence in buffer.finish():
            validate_speech(sentence, messages)
            reporter.mark('sentence_ready')
            speaker.put(sentence)
        emit('transcript', role='assistant', text=answer)
        speaker.finish()
        return answer
    finally:
        speaker.close()


def run(config, max_turns=None):
    config = validate_runtime(config)
    reporter = Reporter(emit)
    backend = llm = recorder = None
    try:
        reporter.set_phase('loading')
        reporter.mark('load_start', language=config['language'], stt_backend=config.get('stt_backend', 'vosk'),
                      llm_model=config['llm'], llm_threads=config['llm_threads'], llm_batch_threads=config['llm_batch_threads'])
        from llama_cpp import Llama
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        backend = create_stt(config)
        reporter.mark('load_stt_end')
        vad = SileroVad(config['vad_model'])
        reporter.mark('load_vad_end')
        llm = Llama(model_path=config['llm'], n_ctx=config['llm_context'],
                    n_threads=config['llm_threads'], n_threads_batch=config['llm_batch_threads'], verbose=False)
        reporter.mark('load_llm_end')
        formatter = make_formatter(llm)
        voice = load_voice(config)
        reporter.mark('load_tts_end')
        from .local_recording import SessionRecording
        recorder = SessionRecording(config.get('recording_root'))
        emit('recording', status='started', id=recorder.id)
        language = {'tr': 'Turkish', 'en': 'English'}.get(config['language'], config['language'])
        system = {'role': 'system', 'content': config['system_prompt'].strip() + f'\nReply in {language} ({config["language"]}).'}
        history = []
        workflow = None
        reply_streamed = False
        if config.get('workflow'):
            from .workflow_inference import make_decider, ToolChannel
            from .workflows import WorkflowEngine
            channel = ToolChannel(emit, config['workflow_session_id'])
            def stream_reply(messages):
                nonlocal reply_streamed
                reply_streamed = True
                return generate_and_speak(config, llm, messages, voice, reporter,
                    sampling={'temperature': .3, 'top_k': 40, 'top_p': .9, 'min_p': 0, 'repeat_penalty': 1}, recorder=recorder)
            workflow = WorkflowEngine(config['workflow'], make_decider(llm,
                config.get('system_prompt', ''), config['language'], reply_generator=stream_reply,
                reply_max_tokens=config['llm_max_tokens'], metric=reporter.mark), channel.call,
                lambda event: emit('workflow_event', event=event))
            emit('workflow_event', event={'type': 'node', 'node_id': workflow.current})
        reporter.mark('load_end')
        # Do not run query-dependent fixed tools with an empty user question.
        opening_turn = bool(workflow and (
            workflow.nodes[workflow.current]['data'].get('prompt',
                workflow.nodes[workflow.current]['data'].get('message', ''))
            or workflow.nodes[workflow.outgoing(workflow.current)[0]['target']]['type'] == 'agent'))
        while max_turns is None or reporter.turn < max_turns:
            reporter.turn += 1
            opening = opening_turn
            opening_turn = False
            text = '' if opening else listen_vad(config, backend, vad, reporter, recorder)
            if not text and not opening:
                continue
            if not opening:
                emit('transcript', role='user', text=text)
            if workflow:
                reply_streamed = False
                channel.turn_id = reporter.turn
                reporter.mark('workflow_start')
                reporter.set_phase('thinking')
                emit('compute', active=True)
                try:
                    result = workflow.turn(text)
                finally:
                    emit('compute', active=False)
                emit('workflow_event', event={'type': 'answer', **result})
                if not reply_streamed:
                    emit('transcript', role='assistant', text=result['text'])
                    if result['text']:
                        sentences = SentenceBuffer()
                        speak_sentences(config, voice,
                            sentences.push(result['text']) + sentences.finish(), reporter, recorder=recorder)
                reporter.mark('workflow_end')
                if result['ended']:
                    emit('workflow_complete')
                    break
                continue
            messages = fit_messages(llm, formatter, system, history, text, config['llm_max_tokens'])
            answer = generate_and_speak(config, llm, messages, voice, reporter, recorder=recorder)
            history = (messages[1:] + [{'role': 'assistant', 'content': answer}])[-4:]
            emit('state', state='connecting', detail='Mikrofon yeniden açılıyor')
    finally:
        if recorder:
            try:
                emit('recording', status='complete', **recorder.close())
            except Exception as error:
                emit('diagnostic', message='Ses kaydı kapatılamadı: ' + str(error))
        try:
            if backend:
                backend.close()
        finally:
            try:
                if llm:
                    llm.close()
            finally:
                reporter.close()


if __name__ == '__main__':
    try:
        run(json.loads(sys.stdin.readline()))
    except Exception as error:
        emit('error', message=str(error))
        sys.exit(1)
