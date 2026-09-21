"""Frame-level VAD decisions on the captured-audio timeline."""
import numpy as np


def frame_trace(pcm, probability, gate, was_active, frames, done, index):
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64) / 32768
    start = gate.active and not was_active
    threshold = gate.config['vad_threshold']
    effective = max(.01, threshold - .15) if was_active else threshold
    return dict(frame=index, audio_start_sec=round(index * .032, 3),
                probability=round(probability, 6), threshold=effective,
                rms_dbfs=round(10 * float(np.log10(max(float(np.mean(samples ** 2)), 1e-12))), 2),
                peak=round(float(np.max(np.abs(samples))), 5),
                active=gate.active, above_threshold=probability >= effective,
                consecutive_start_frames=gate.voiced, silence_ms=gate.silent * 32,
                emitted_frames=len(frames), event='start' if start else 'end' if done else 'frame',
                retained_from_sec=round((index + 1 - len(frames)) * .032, 3) if frames else None,
                endpoint_reason=('silence' if gate.silent * 32 >= gate.config['vad_silence_ms']
                                 else 'max_duration') if done else None)
