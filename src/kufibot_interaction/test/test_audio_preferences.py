import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction import audio_devices as audio, audio_preferences as preferences, voice_aec


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_DEVICE_SETTINGS', str(tmp_path / 'devices.json'))


def test_atomic_private_persistence_and_validation(tmp_path):
    assert preferences.read_preferences() == dict(microphone='', speaker='')
    value = dict(microphone='pulse:usb_mic', speaker='bluetooth:04:57:91:5A:8E:B7')
    preferences.save_preferences(value)
    assert preferences.read_preferences() == value
    assert preferences.settings_path().stat().st_mode & 0o777 == 0o600
    for invalid in ('pulse:kufibot_aec_sink', 'pulse:output.monitor', 'pulse:foo\nbar', 'bluetooth:BAD'):
        with pytest.raises(ValueError):
            preferences.save_preferences({**value, 'speaker': invalid})
    assert preferences.read_preferences() == value


def test_direct_audio_and_speech_use_selections_and_bluetooth_stable_id(monkeypatch):
    preferences.save_preferences(dict(microphone='pulse:usb_mic', speaker='bluetooth:04:57:91:5A:8E:B7'))
    async def devices(kind):
        return [{'name': 'usb_mic'}] if kind == 'sources' else [{'name': audio.SPEAKER_PREFIX + '.9'}]
    monkeypatch.setattr(audio, 'pulse_devices', devices)
    expected = ('pulse:usb_mic', 'pulse:' + audio.SPEAKER_PREFIX + '.9')
    for mode in ('disabled', 'system'):
        assert asyncio.run(audio.local_audio_devices(mode, 'old-mic', 'old-speaker')) == expected
    assert asyncio.run(audio.playback_device('old-output')) == expected[1]
    monkeypatch.setattr(audio, 'pulse_devices', AsyncMock(return_value=[]))
    with pytest.raises(RuntimeError, match='bağlı değil'):
        asyncio.run(audio.playback_device('pulse:default'))


def test_aec_routes_new_targets_once_and_preserves_tuning(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    path = voice_aec.config_path()
    path.parent.mkdir(parents=True)
    path.write_text(voice_aec.configuration(300, noise_suppression=False, node_latency='1920/48000'))
    restart = Mock()
    monkeypatch.setattr(voice_aec.subprocess, 'run', restart)
    preferences.save_preferences(dict(microphone='pulse:new_mic', speaker='pulse:new_speaker'))
    async def devices(kind):
        return [{'name': name} for name in (('new_mic', 'kufibot_aec_source') if kind == 'sources'
                                            else ('new_speaker', 'kufibot_aec_sink'))]
    monkeypatch.setattr(audio, 'pulse_devices', devices)
    assert asyncio.run(audio.local_audio_devices('enabled', 'old', 'old')) == audio.AEC_PAIR
    content = path.read_text()
    assert 'target.object = "new_mic"' in content and 'target.object = "new_speaker"' in content
    assert '300/1000' in content and '1920/48000' in content and 'noise_suppression = false' in content
    assert voice_aec.is_managed(content)
    asyncio.run(audio.local_audio_devices('enabled', 'old', 'old'))
    restart.assert_called_once()
    # Reconfiguring DSP later must retain device selections.
    import sys
    monkeypatch.setattr(sys, 'argv', ['voice_aec', '--play-delay-ms', '0'])
    voice_aec.main()
    assert 'target.object = "new_mic"' in path.read_text()
    assert 'target.object = "new_speaker"' in path.read_text()
    # Clearing selections must also restore the original physical AEC targets.
    preferences.save_preferences(dict(microphone='', speaker=''))
    async def defaults(kind):
        return [{'name': name} for name in ((audio.CAMERA_SOURCE, 'kufibot_aec_source') if kind == 'sources'
                                            else (audio.SPEAKER_PREFIX + '.2', 'kufibot_aec_sink'))]
    monkeypatch.setattr(audio, 'pulse_devices', defaults)
    assert asyncio.run(audio.local_audio_devices('enabled', *audio.AEC_PAIR)) == audio.AEC_PAIR
    assert f'target.object = "{audio.CAMERA_SOURCE}"' in path.read_text()
    assert f'target.object = "{audio.SPEAKER_PREFIX}.2"' in path.read_text()


def test_selected_aec_output_volume_does_not_change_other_speakers(monkeypatch):
    preferences.save_preferences(dict(microphone='', speaker='pulse:usb_speaker'))
    devices = [dict(name=name, mute=False, volume={'mono': {'value': 100}})
               for name in ('kufibot_aec_sink', 'usb_speaker', audio.SPEAKER_PREFIX + '.1')]
    monkeypatch.setattr(audio, 'pulse_devices', AsyncMock(return_value=devices))
    command = AsyncMock()
    monkeypatch.setattr(audio, 'pactl_output', command)
    asyncio.run(audio.ensure_playback_volume(audio.AEC_PAIR[1]))
    assert [call.args[1] for call in command.await_args_list] == ['kufibot_aec_sink', 'usb_speaker']
