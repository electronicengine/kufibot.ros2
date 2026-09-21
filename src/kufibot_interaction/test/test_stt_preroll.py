"""Show how delayed VAD activation affects the audio sent to STT."""
from kufibot_interaction.local_voice_runtime import DEFAULTS, SpeechGate


def test_longer_preroll_preserves_quiet_onset_when_vad_triggers_late():
    # 640 ms quiet speech followed by 96 ms confident speech.
    audio = [index.to_bytes(2, 'little') * 512 for index in range(23)]
    outputs = []
    for milliseconds in (300, 1500):
        gate = SpeechGate({**DEFAULTS, 'vad_pre_roll_ms': milliseconds})
        received = []
        for index, frame in enumerate(audio):
            frames, _ = gate.push(frame, .1 if index < 20 else .9, index * .032)
            received.extend(frames)
        outputs.append(received)
    assert outputs[0] == audio[12:]
    assert outputs[1] == audio
