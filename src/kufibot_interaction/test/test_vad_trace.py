from kufibot_interaction.local_voice_runtime import DEFAULTS, SpeechGate
from kufibot_interaction.vad_trace import frame_trace


def test_trace_locates_retained_start_and_silence_endpoint():
    gate = SpeechGate(DEFAULTS)
    rows = []
    for index, probability in enumerate([.01] * 20 + [.9] * 3 + [.01] * 19):
        pcm = bytes(1024)
        before = gate.active
        frames, done = gate.push(pcm, probability, index * .032)
        rows.append(frame_trace(pcm, probability, gate, before, frames, done, index))
    start = rows[22]
    assert start['event'] == 'start'
    assert start['retained_from_sec'] == .384
    assert start['emitted_frames'] == 11
    assert start['threshold'] == .5
    assert rows[-1]['threshold'] == .35
    assert rows[-1]['event'] == 'end'
    assert rows[-1]['endpoint_reason'] == 'silence'
    assert rows[-1]['silence_ms'] == 608
    assert rows[0]['rms_dbfs'] == -120
    assert rows[0]['retained_from_sec'] is None


def test_trace_distinguishes_duration_limit():
    gate = SpeechGate({**DEFAULTS, 'max_utterance_sec': .128})
    for index in range(4):
        before = gate.active
        frames, done = gate.push(bytes(1024), .9, index * .032)
    row = frame_trace(bytes(1024), .9, gate, before, frames, done, index)
    assert row['endpoint_reason'] == 'max_duration'
