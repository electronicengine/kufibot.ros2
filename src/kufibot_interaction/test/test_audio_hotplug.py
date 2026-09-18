"""USB audio recovery must preserve the active conversation and RTP track."""
import asyncio
from unittest.mock import AsyncMock, Mock

from kufibot_interaction.audio import AlsaMicTrack
from kufibot_interaction.voice_agent_node import VoiceAgentNode


def test_watch_audio_preserves_session_during_disconnect(monkeypatch):
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    session = object()
    node.session = session
    node.stopping = False
    node.mic_device = node.speaker_device = 'pulse:test'
    node.mic = Mock(capture_error=None, suspended=False)
    node.speakers = [Mock(error=None, suspended=False)]
    node._stop_session = AsyncMock()
    node._start_session = AsyncMock()
    node._publish_state = Mock()
    node.get_logger = Mock()
    count = 0

    async def check(*args):
        nonlocal count
        count += 1
        assert node.session is session
        if count <= 12:
            raise RuntimeError('AEC camera source is unavailable')
        if count == 13:
            assert node.mic.suspended
        if count == 14:
            assert not node.mic.suspended
            raise asyncio.CancelledError

    monkeypatch.setattr('kufibot_interaction.audio.check_aec_devices', check)
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.asyncio.sleep', AsyncMock())
    asyncio.run(node._watch_audio(session))
    node._stop_session.assert_not_awaited()
    node._start_session.assert_not_awaited()
    assert node.session is session
    assert not node.speakers[0].suspended


def test_watch_audio_repairs_only_local_streams(monkeypatch):
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    session = object()
    node.session = session
    node.stopping = False
    node.mic_device = node.speaker_device = 'pulse:test'
    node.mic = Mock(capture_error='USB removed', restart_capture=AsyncMock())
    speaker = Mock(error='Broken pipe', restart_playback=AsyncMock())
    node.speakers = [speaker]
    check = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr('kufibot_interaction.audio.check_aec_devices', check)
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.asyncio.sleep', AsyncMock())
    asyncio.run(node._watch_audio(session))
    node.mic.restart_capture.assert_awaited_once()
    speaker.restart_playback.assert_awaited_once()
    assert node.session is session


def test_mic_failure_sends_silence_then_resumes_same_track():
    async def scenario():
        mic = AlsaMicTrack('pulse:test')
        mic.capture_error = 'USB removed'
        mic.queue.put_nowait(None)
        first = await mic.recv()
        assert not any(bytes(first.planes[0]))
        assert mic.readyState == 'live'
        mic.capture_error = None
        mic.queue.put_nowait(b'\x01\x00' * 160)
        second = await mic.recv()
        assert second.pts == first.pts + 160
        assert bytes(second.planes[0]) == b'\x01\x00' * 160
        await mic.close_capture()
    asyncio.run(scenario())


def test_recorder_restart_preserves_track_and_timestamp():
    async def scenario():
        mic = AlsaMicTrack('pulse:test')
        mic.samples_sent = 1600
        mic.capture_error = 'USB removed'
        mic.queue.put_nowait(b'old PCM')
        mic._close_recorder = AsyncMock()
        mic._start_capture = AsyncMock()
        await mic.restart_capture()
        assert mic.readyState == 'live'
        assert mic.samples_sent == 1600
        assert mic.capture_error is None
        assert mic.queue.empty()
        mic._start_capture.assert_awaited_once()
        await mic.close_capture()
    asyncio.run(scenario())


def test_real_recorder_restart_keeps_rtp_alive(monkeypatch):
    async def scenario():
        processes = []
        for value in (1, 2):
            process = Mock(returncode=None, wait=AsyncMock())
            process.stdout = asyncio.StreamReader()
            process.stderr = asyncio.StreamReader()
            process.stdout.feed_data(bytes([value, 0]) * 160)
            process.stderr.feed_eof()
            processes.append(process)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec',
                            AsyncMock(side_effect=processes))
        mic = AlsaMicTrack('pulse:test', noise_gate_rms=0)
        try:
            await mic.start_capture()
            first = await mic.recv()
            processes[0].stdout.feed_eof()
            silence = await mic.recv()
            assert mic.capture_error
            assert not any(bytes(silence.planes[0]))
            await mic.restart_capture()
            resumed = await mic.recv()
            assert bytes(resumed.planes[0]) == b'\x02\x00' * 160
            assert resumed.pts == first.pts + 320
            assert mic.readyState == 'live'
        finally:
            await mic.close_capture()
    asyncio.run(scenario())
