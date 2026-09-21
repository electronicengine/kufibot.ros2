"""Fixed gain for PCM16 microphone experiments, with saturation accounting."""
import math


class PcmGain:
    def __init__(self, db=0):
        if not math.isfinite(db) or not 0 <= db <= 24:
            raise ValueError('Gain must be between 0 and 24 dB')
        self.factor = 10 ** (db / 20)
        self.samples = self.clipped = 0

    def __call__(self, pcm):
        import numpy as np
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64) * self.factor
        self.samples += samples.size
        self.clipped += int(np.count_nonzero((samples > 32767) | (samples < -32768)))
        if self.factor == 1:
            return pcm
        return np.clip(np.rint(samples), -32768, 32767).astype('<i2').tobytes()
