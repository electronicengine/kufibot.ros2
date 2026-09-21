#!/usr/bin/env python3
"""Interactive STT-only probe using the agent's microphone/VAD/backend code.

Run with .venv/bin/python tools/local_voice/test_microphone_stt.py --language tr
Replay a saved utterance with --wav PATH (no microphone or VAD).
"""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/kufibot_interaction'))
from kufibot_interaction import local_voice_worker as worker
from kufibot_interaction.local_voice_runtime import DEFAULTS, SileroVad, SessionDeadline, SessionTimeout
from kufibot_interaction.pcm_gain import PcmGain


class Probe:
    def __init__(self, directory, debug_vad=False):
        self.directory = directory
        self.turn = 0
        self.stream = None
        self.raw = self.trace_file = None
        self.debug_vad = debug_vad
        self.last_capture_at = None
        self.events = (directory / 'events.jsonl').open('a')

    def emit(self, kind, **values):
        self.events.write(json.dumps(dict(type=kind, turn=self.turn, monotonic_sec=time.monotonic(), **values), ensure_ascii=False) + '\n')
        self.events.flush()
        if kind == 'ready':
            print('\nDİNLİYOR — konuşabilirsiniz; bitince kısa süre susun.', flush=True)
        elif kind == 'transcript':
            print(('SONUÇ: ' if values.get('final') else 'Ara metin: ') + values['text'], flush=True)
        elif kind == 'diagnostic':
            print(values['message'], flush=True)
        elif kind == 'metric' and values['event'] == 'stt_final':
            print('Ölçümler: ' + json.dumps(values, ensure_ascii=False), flush=True)

    def set_phase(self, phase):
        self.emit('phase', phase=phase)

    def mark(self, name, **values):
        self.emit('metric', event=name, **values)

    def begin_utterance(self, role):
        self.close_wav()
        self.turn += 1
        self.stream = wave.open(str(self.directory / f'utterance-{self.turn:03}.wav'), 'wb')
        self.stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        if self.debug_vad:
            self.raw = wave.open(str(self.directory / f'capture-{self.turn:03}.wav'), 'wb')
            self.raw.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            self.trace_file = (self.directory / f'vad-{self.turn:03}.jsonl').open('w')

    def trace(self, pcm, row):
        now = time.monotonic()
        if row['frame'] == 0 and self.last_capture_at is not None:
            self.emit('diagnostic', message=f'Önceki turun son karesi ile yeni turun ilk karesi arası: '
                      f'{(now - self.last_capture_at) * 1000:.0f} ms (ses alımı dışındaki işlem sürelerini de içerir)')
        self.last_capture_at = now
        row['monotonic_sec'] = now
        self.raw.writeframes(pcm)
        self.trace_file.write(json.dumps(row) + '\n')
        if row['event'] != 'frame' or row['frame'] % 16 == 0:
            self.trace_file.flush()
            print(f"VAD t={row['audio_start_sec']:.2f}s p={row['probability']:.3f} "
                  f"eşik={row['threshold']:.2f} RMS={row['rms_dbfs']:.1f} dBFS "
                  f"olay={row['event']} sessizlik={row['silence_ms']}ms "
                  f"STT_kare={row['emitted_frames']} başlangıç={row['retained_from_sec']} "
                  f"bitiş_nedeni={row['endpoint_reason']}", flush=True)

    def write(self, role, pcm):
        self.stream.writeframes(pcm)

    def close_wav(self):
        for name in ('raw', 'trace_file'):
            handle = getattr(self, name)
            if handle:
                handle.close()
                setattr(self, name, None)
        if self.stream:
            self.stream.close()
            self.stream = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['tr', 'en'], default='tr')
    parser.add_argument('--backend', choices=['vosk', 'hailo_whisper'], default='vosk')
    parser.add_argument('--model', help='Override the model directory')
    parser.add_argument('--mic', help='Override the robot microphone')
    parser.add_argument('--gain-db', type=float, default=0, help='Gain before VAD and STT (0–24 dB); start with 6')
    parser.add_argument('--debug-vad', action='store_true', help='Save all captured PCM before gating and per-frame VAD decisions')
    parser.add_argument('--turns', type=int, default=0, help='0: listen until Ctrl+C')
    parser.add_argument('--timeout', type=float, default=0, help='Total listening timeout in seconds; 0: until Ctrl+C')
    parser.add_argument('--vad-threshold', type=float, help='Speech probability threshold (0 to 1)')
    parser.add_argument('--vad-silence-ms', type=int, help='Silence before final result, e.g. 600')
    parser.add_argument('--vad-pre-roll-ms', type=int, help='Audio retained before VAD activation, e.g. 1500')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--wav', type=Path, help='Replay a 16 kHz mono PCM16 WAV')
    args = parser.parse_args()
    if not math.isfinite(args.gain_db) or not 0 <= args.gain_db <= 24:
        parser.error('gain-db must be between 0 and 24')
    if args.turns < 0 or not math.isfinite(args.timeout) or args.timeout < 0:
        parser.error('turns >= 0 and finite timeout >= 0 required')
    if args.vad_threshold is not None and not 0 < args.vad_threshold < 1:
        parser.error('vad-threshold must be between 0 and 1')
    if args.vad_silence_ms is not None and not 32 <= args.vad_silence_ms <= 5000:
        parser.error('vad-silence-ms must be between 32 and 5000')
    if args.vad_pre_roll_ms is not None and not 32 <= args.vad_pre_roll_ms <= 2000:
        parser.error('vad-pre-roll-ms must be between 32 and 2000')
    import yaml
    params = yaml.safe_load((ROOT / 'src/kufibot_bringup/config/interactive_robot.yaml').read_text())['voice_agent_node']['ros__parameters']
    config = {**DEFAULTS, **{k[6:]: v for k, v in params.items() if k.startswith('local_')}}
    for key in ('vad_threshold', 'vad_silence_ms', 'vad_pre_roll_ms'):
        if getattr(args, key) is not None:
            config[key] = getattr(args, key)
    model = ('trRecognizeModel' if args.language == 'tr' else 'engRecognizeModel') if args.backend == 'vosk' else 'whisper-base-hailo8l'
    config.update(language=args.language, stt_backend=args.backend,
                  stt=args.model or '/usr/local/ai.models/' + model, mic=args.mic or params['mic_device'])
    config['test_gain_db'] = args.gain_db
    directory = args.output or ROOT / 'log/stt-probe' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps(config, indent=2))
    probe = Probe(directory, args.debug_vad)
    worker.emit = probe.emit
    backend = None
    deadline = SessionDeadline(args.timeout)
    try:
        print(f"STT: {config['stt']} | Mikrofon: {config['mic']}\nKayıtlar: {directory}", flush=True)
        print(f'Giriş kazancı: +{args.gain_db:g} dB (VAD + STT)', flush=True)
        print(f"VAD açık | eşik={config['vad_threshold']} | ön tampon={config['vad_pre_roll_ms']} ms | bitiş sessizliği={config['vad_silence_ms']} ms | Çıkış: Ctrl+C", flush=True)
        if args.backend == 'vosk':
            from vosk import SetLogLevel
            SetLogLevel(-1)
        else:
            print('Hailo yalnızca konuşma sonunda sonuç verir; canlı ara metin için --backend vosk kullanın.', flush=True)
        backend = worker.create_stt(config)
        gain = PcmGain(args.gain_db)
        if args.wav:
            with wave.open(str(args.wav), 'rb') as source:
                if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (16000, 1, 2, 'NONE'):
                    raise ValueError('WAV must be 16 kHz mono PCM16')
                while pcm := source.readframes(512):
                    backend.feed(gain(pcm))
            probe.emit('transcript', text=backend.finish(), final=True)
        else:
            vad = SileroVad(config['vad_model'])
            deadline.start()
            while not args.turns or probe.turn < args.turns:
                gain = PcmGain(args.gain_db)
                text = worker.listen_vad(config, backend, vad, probe, probe, pcm_transform=gain,
                                         trace=probe.trace if args.debug_vad else None)
                probe.close_wav()
                probe.emit('transcript', text=text, final=True)
                probe.emit('diagnostic', message=f'Kazançta kırpılan örnek: {gain.clipped}/{gain.samples}'
                           + (' — --gain-db değerini azaltın.' if gain.clipped else ''))
    except (KeyboardInterrupt, SessionTimeout):
        print('\nSTT testi durduruldu.', flush=True)
    finally:
        deadline.close()
        probe.close_wav()
        probe.events.close()
        if backend:
            backend.close()


if __name__ == '__main__':
    main()
