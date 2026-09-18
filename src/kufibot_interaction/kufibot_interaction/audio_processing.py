"""Optional streaming noise reduction after the device's acoustic echo canceller."""

import ctypes
import ctypes.util
from collections import deque
import math


class PlaybackBargeInGuard:
    """Require sustained near-end energy during playback and its echo tail.

    A small, constant lookahead preserves the onset of an accepted interruption.
    This energy guard precedes server VAD; it does not identify the speaker or
    replace AEC. Outside playback protection even quiet audio passes unchanged.
    """

    def __init__(self, rms_threshold, start_sec=0.18, release_sec=0.4,
                 frame_sec=0.01):
        if not math.isfinite(rms_threshold) or not 0 < rms_threshold <= 32768:
            raise ValueError('Barge-in RMS must be between 0 and 32768 (exclusive of 0)')
        if not math.isfinite(start_sec) or not frame_sec <= start_sec <= 0.5:
            raise ValueError('Barge-in start duration must be between one frame and 0.5 s')
        self.threshold = rms_threshold
        self.start_frames = math.ceil(start_sec / frame_sec)
        self.release_frames = math.ceil(release_sec / frame_sec)
        self.recent = deque(maxlen=self.start_frames)
        self.pending = deque()
        self.open_frames = 0
        self.suppressed_frames = 0
        self.was_protected = False

    def process(self, pcm, rms, protected):
        # Listening-mode speech must not pre-open the higher playback gate.
        # Keep buffered frames' decisions so an ending echo tail cannot leak.
        if protected != self.was_protected:
            self.recent.clear()
            self.open_frames = 0
            self.was_protected = protected
        self.pending.append([pcm, not protected])
        self.recent.append(protected and rms >= self.threshold)
        # Require energy in at least 75% of the window: an isolated servo click
        # cannot open the gate, while short gaps in speech are tolerated.
        confirmed = (len(self.recent) == self.start_frames
                     and sum(self.recent) >= math.ceil(self.start_frames * 0.75)
                     and self.recent[-1])
        if confirmed:
            self.open_frames = self.release_frames
            for frame in self.pending:
                frame[1] = True
        if self.open_frames > 0:
            self.pending[-1][1] = True
            self.open_frames -= 1
        if len(self.pending) < self.start_frames:
            return None
        pcm, allowed = self.pending.popleft()
        if not allowed:
            self.suppressed_frames += 1
            return bytes(len(pcm))
        return pcm


class SpeexDenoiser:
    """Suppress estimated background noise without muting during robot speech.

    SpeexDSP owns one adaptive state per capture stream. AGC and VAD gating
    remain disabled: they can amplify motor noise or cut quiet user speech.
    This is a noise postfilter, not a replacement for reference-based AEC.
    """

    def __init__(self, sample_rate=16000, frame_samples=160, suppression_db=-25):
        if not -60 <= suppression_db <= 0:
            raise ValueError('Noise suppression must be between -60 and 0 dB')
        library = ctypes.util.find_library('speexdsp')
        if not library:
            raise RuntimeError('Noise reduction requires libspeexdsp1 (apt install libspeexdsp1)')
        self.lib = ctypes.CDLL(library)
        self.lib.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
        self.lib.speex_preprocess_state_init.restype = ctypes.c_void_p
        self.lib.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
        self.lib.speex_preprocess_state_destroy.restype = None
        self.lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self.lib.speex_preprocess_ctl.restype = ctypes.c_int
        self.lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16)]
        self.lib.speex_preprocess_run.restype = ctypes.c_int
        self.frame_samples = frame_samples
        self.state = self.lib.speex_preprocess_state_init(frame_samples, sample_rate)
        if not self.state:
            raise RuntimeError('Unable to initialize microphone noise reduction')
        try:
            # SPEEX_PREPROCESS_SET_DENOISE / AGC / NOISE_SUPPRESS.
            # VAD is off by default. Its deprecated setter warns even when
            # disabling it, so leave that default alone.
            for request, value in ((0, 1), (2, 0), (18, int(suppression_db))):
                setting = ctypes.c_int(value)
                if self.lib.speex_preprocess_ctl(self.state, request, ctypes.byref(setting)):
                    raise RuntimeError('Unable to configure microphone noise reduction')
        except BaseException:
            self.close()
            raise

    def process(self, pcm):
        if not self.state:
            raise RuntimeError('Microphone noise reduction is closed')
        if len(pcm) != self.frame_samples * 2:
            raise ValueError('Noise reduction requires exactly one PCM frame')
        samples = (ctypes.c_int16 * self.frame_samples).from_buffer_copy(pcm)
        self.lib.speex_preprocess_run(self.state, samples)
        return bytes(samples)

    def close(self):
        if self.state:
            self.lib.speex_preprocess_state_destroy(self.state)
            self.state = None
