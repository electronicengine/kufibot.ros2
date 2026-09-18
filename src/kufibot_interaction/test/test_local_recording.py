import json
import wave

from kufibot_interaction.local_recording import SessionRecording


def test_session_recording_merges_user_and_robot_into_stereo_wav(tmp_path):
    recording = SessionRecording(tmp_path)
    recording.write('user', b'\x01\x00' * 160)
    recording.write('assistant', b'\x02\x00' * 160, 16000)
    info = recording.close()
    assert info['channels'] == {'left': 'user', 'right': 'assistant'}
    assert json.loads((tmp_path / f"{info['id']}.json").read_text())['id'] == info['id']
    with wave.open(str(tmp_path / f"{info['id']}.wav"), 'rb') as sound:
        assert (sound.getnchannels(), sound.getframerate()) == (2, 16000)
        assert sound.getnframes() >= 160
        data = sound.readframes(sound.getnframes())
        assert b'\x01\x00' in data and b'\x02\x00' in data
