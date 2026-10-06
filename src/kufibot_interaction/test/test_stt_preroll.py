"""Show how delayed VAD activation affects the audio sent to STT."""
from kufibot_interaction.local_voice_runtime import DEFAULTS, SpeechGate


def test_short_confidence_bursts_do_not_accumulate_into_speech():
    gate = SpeechGate(DEFAULTS)
    for index, probability in enumerate([.9, .9, .1] * 5):
        frames, done = gate.push(bytes(1024), probability, index * .032)
        assert frames == []
        assert not done
    assert not gate.active
    for index in range(3):
        gate.push(bytes(1024), .9, 1 + index * .032)
    assert gate.active


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
