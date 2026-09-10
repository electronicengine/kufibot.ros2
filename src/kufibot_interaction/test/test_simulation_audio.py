import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction.audio import AlsaMicTrack, audio_command


def test_wsl_audio_uses_default_pulse_source_and_sink():
    assert audio_command(True, 'pulse:default', 16000, 1) == [
        'parec', '--raw', '--format=s16le', '--rate=16000',
        '--channels=1', '--latency-msec=20']
    assert audio_command(False, 'pulse:my_sink', 48000, 2)[-1] == '--device=my_sink'
    assert audio_command(False, 'pulse:default', 48000, 2)[0] == 'pacat'


def test_hardware_audio_keeps_alsa_device(monkeypatch):
    # A desktop environment variable must not change the real robot's backend.
    monkeypatch.setenv('PULSE_SERVER', 'unix:/mnt/wslg/PulseServer')
    assert audio_command(True, 'plughw:2,0', 16000, 1) == [
        'arecord', '-D', 'plughw:2,0', '-f', 'S16_LE', '-r', '16000',
        '-c', '1', '-t', 'raw', '--buffer-time=200000', '--period-time=20000']
    assert audio_command(False, 'default', 48000, 2) == [
        'aplay', '-D', 'default', '-f', 'S16_LE', '-r', '48000',
        '-c', '2', '-t', 'raw']


def test_missing_microphone_fails_before_connect(monkeypatch):
    async def run():
        process = Mock()
        process.stdout = asyncio.StreamReader()
        process.stdout.feed_eof()
        process.stderr = asyncio.StreamReader()
        process.stderr.feed_eof()
        process.returncode = 1
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        mic = AlsaMicTrack('pulse:default')
        with pytest.raises(RuntimeError, match='Microphone capture ended'):
            await mic.start_capture()
        assert mic.readyState == 'ended'
    asyncio.run(run())


@pytest.mark.parametrize('device', ['plughw:2,0', 'pulse:default'])
def test_capture_preserves_pcm_and_continuous_timestamps(monkeypatch, device):
    async def run():
        process = Mock()
        process.stdout = asyncio.StreamReader()
        pcm = b'\x00\x10' * 160
        process.stdout.feed_data(pcm * 2)
        process.stderr = asyncio.StreamReader()
        process.stderr.feed_eof()
        process.returncode = None
        process.wait = AsyncMock()
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        mic = AlsaMicTrack(device)
        try:
            await mic.start_capture()
            first, second = await mic.recv(), await mic.recv()
            assert bytes(first.planes[0]) == pcm
            assert bytes(second.planes[0]) == pcm
            assert first.sample_rate == second.sample_rate == 16000
            assert (first.pts, second.pts) == (0, 160)
        finally:
            await mic.close_capture()
        process.terminate.assert_called_once()
    asyncio.run(run())
