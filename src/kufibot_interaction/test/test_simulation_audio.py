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


def test_capture_disconnect_keeps_receiver_alive_until_explicit_close(monkeypatch):
    from aiortc.mediastreams import MediaStreamError
    async def run():
        process = Mock()
        process.stdout = asyncio.StreamReader()
        process.stderr = asyncio.StreamReader()
        process.stdout.feed_data(b'\x00\x10' * 160)
        process.stderr.feed_eof()
        process.returncode = None
        process.wait = AsyncMock()
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        mic = AlsaMicTrack('pulse:test', noise_gate_rms=0)
        try:
            await mic.start_capture()
            mic.bot_speaking = True
            frame = await mic.recv()
            assert bytes(frame.planes[0]) == b'\x00\x10' * 160
            process.stdout.feed_eof()
            silence = await asyncio.wait_for(mic.recv(), 0.2)
            assert not any(bytes(silence.planes[0]))
            assert silence.pts == frame.pts + 160
            assert mic.readyState == 'live'
            await mic.close_capture()
            with pytest.raises(MediaStreamError):
                await asyncio.wait_for(mic.recv(), 0.2)
        finally:
            await mic.close_capture()
    asyncio.run(run())


def test_speaker_expires_speaking_without_next_packet():
    from kufibot_interaction.audio import AlsaSpeaker
    async def run():
        track = Mock()
        track.recv = AsyncMock(side_effect=lambda: None)
        async def pending():
            await asyncio.Event().wait()
        track.recv.side_effect = pending
        mic = Mock(bot_speaking=False)
        callback = Mock()
        speaker = AlsaSpeaker(track, 'pulse:test', mic, callback)
        try:
            speaker._update_speaking(b'\x00\x10' * 160)
            assert speaker.speaking
            await asyncio.sleep(0.45)
            assert not speaker.speaking
            assert not mic.bot_speaking
        finally:
            await speaker.close()
    asyncio.run(run())


def test_interrupt_discards_player_but_keeps_receiving():
    from kufibot_interaction.audio import AlsaSpeaker
    async def run():
        track = Mock()
        async def pending():
            await asyncio.Event().wait()
        track.recv = AsyncMock(side_effect=pending)
        speaker = AlsaSpeaker(track, 'pulse:test', Mock())
        player = Mock(returncode=None, wait=AsyncMock())
        speaker.process = player
        try:
            await speaker.interrupt()
            player.terminate.assert_called_once()
            assert speaker.process is None
            assert not speaker.task.done()
        finally:
            await speaker.close()
    asyncio.run(run())


def test_legacy_pulse_alias_uses_native_pulse_playback():
    assert audio_command(False, 'pulse', 48000, 1) == audio_command(False, 'pulse:default', 48000, 1)


def test_bluetooth_startup_longer_than_half_second_does_not_abort(monkeypatch):
    from kufibot_interaction.audio import AlsaSpeaker
    from av import AudioFrame
    async def run():
        frame = AudioFrame(format='s16', layout='mono', samples=960)
        frame.planes[0].update(b'\x00\x10' * 960)
        frame.sample_rate = 48000
        delivered = asyncio.Event()
        async def drain():
            await asyncio.sleep(0.65)
            delivered.set()
        process = Mock(returncode=None, wait=AsyncMock())
        process.stdin.drain = drain
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        count = 0
        async def recv():
            nonlocal count
            count += 1
            if count == 1:
                return frame
            await asyncio.Event().wait()
        speaker = AlsaSpeaker(Mock(recv=recv), 'pulse', Mock())
        try:
            await asyncio.wait_for(delivered.wait(), 1.5)
            assert speaker.error is None
            assert not speaker.task.done()
        finally:
            await speaker.close()
    asyncio.run(run())


def test_interrupt_does_not_wait_for_blocked_bluetooth_writer(monkeypatch):
    from kufibot_interaction.audio import AlsaSpeaker
    from av import AudioFrame
    async def run():
        frame = AudioFrame(format='s16', layout='mono', samples=960)
        frame.planes[0].update(b'\x00\x10' * 960)
        frame.sample_rate = 48000
        writing, terminated = asyncio.Event(), asyncio.Event()
        async def drain():
            writing.set()
            await terminated.wait()
            raise BrokenPipeError
        process = Mock(returncode=None, wait=AsyncMock(), terminate=terminated.set)
        process.stdin.drain = drain
        count = 0
        async def recv():
            nonlocal count
            count += 1
            if count == 1:
                return frame
            await asyncio.Event().wait()
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        speaker = AlsaSpeaker(Mock(recv=recv), 'pulse', Mock())
        try:
            await writing.wait()
            await asyncio.wait_for(speaker.interrupt(), 0.2)
            assert terminated.is_set()
            assert speaker.error is None
        finally:
            await speaker.close()
    asyncio.run(run())
