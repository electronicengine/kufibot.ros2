import asyncio
from unittest.mock import AsyncMock

from kufibot_interaction import audio_devices


def sink(name, volume=65536, mute=False):
    return {'name': name, 'mute': mute, 'volume': {'mono': {'value': volume}}}


def test_aec_playback_restores_physical_and_virtual_volume_without_mic(monkeypatch):
    physical = audio_devices.SPEAKER_PREFIX + '.1'
    devices = [sink('kufibot_aec_sink', 40000), sink(physical, 42598, True),
               sink('unrelated-speaker', 20000)]
    command = AsyncMock()
    monkeypatch.setattr(audio_devices, 'pulse_devices', AsyncMock(return_value=devices))
    monkeypatch.setattr(audio_devices, 'pactl_output', command)

    asyncio.run(audio_devices.ensure_playback_volume('pulse:kufibot_aec_sink'))

    assert [call.args for call in command.await_args_list] == [
        ('set-sink-volume', 'kufibot_aec_sink', '100%'),
        ('set-sink-mute', physical, '0'),
        ('set-sink-volume', physical, '100%')]


def test_unity_volume_is_preserved_without_repeated_writes(monkeypatch):
    command = AsyncMock()
    monkeypatch.setattr(audio_devices, 'pulse_devices',
                        AsyncMock(return_value=[sink('speaker')]))
    monkeypatch.setattr(audio_devices, 'pactl_output', command)
    asyncio.run(audio_devices.ensure_playback_volume('pulse:speaker'))
    command.assert_not_awaited()


def test_playback_stream_starts_at_full_volume_without_affecting_capture():
    assert '--volume=65536' in audio_devices.audio_command(False, 'pulse', 48000, 1)
    assert '--volume=65536' not in audio_devices.audio_command(True, 'pulse', 16000, 1)
