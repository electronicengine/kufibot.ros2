import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction.audio import AlsaMicTrack, AlsaSpeaker
from kufibot_interaction.audio_processing import PlaybackBargeInGuard


def pcm(level):
    return int(level).to_bytes(2, 'little', signed=True) * 160


def feed(guard, levels, protected=True):
    return [result for level in levels
            if (result := guard.process(pcm(level), abs(level), protected)) is not None]


def test_weak_echo_and_isolated_servo_impulse_do_not_open_guard():
    guard = PlaybackBargeInGuard(1200)
    result = feed(guard, [500] * 30 + [16000] + [500] * 100)
    assert result and all(frame == bytes(320) for frame in result)


def test_confirmed_interruption_preserves_first_syllable():
    guard = PlaybackBargeInGuard(1200)
    result = feed(guard, [100] * 30 + [2400] * 40 + [0] * 60)
    assert result.count(pcm(2400)) == 40
    assert result[-1] == bytes(320)


def test_quiet_user_speech_outside_playback_is_unchanged():
    guard = PlaybackBargeInGuard(1200)
    result = feed(guard, [90] * 50, protected=False)
    assert result and all(frame == pcm(90) for frame in result)


def test_short_gaps_do_not_cut_an_accepted_interruption():
    guard = PlaybackBargeInGuard(1200)
    levels = [2400] * 30 + [150] * 20 + [2400] * 30 + [0] * 30
    result = feed(guard, levels)
    assert result[:80] == [pcm(level) for level in levels[:80]]


def test_gate_closes_again_after_the_release_window():
    guard = PlaybackBargeInGuard(1200)
    result = feed(guard, [2400] * 30 + [300] * 100)
    assert result[-20:] == [bytes(320)] * 20


def test_protection_does_not_leak_buffered_echo_when_tail_ends():
    guard = PlaybackBargeInGuard(1200)
    before = feed(guard, [300] * 50)
    after = feed(guard, [90] * 50, protected=False)
    assert all(frame == bytes(320) for frame in before)
    assert after[:guard.start_frames - 1] == [bytes(320)] * (guard.start_frames - 1)
    assert all(frame == pcm(90) for frame in after[guard.start_frames - 1:])


@pytest.mark.parametrize('rms,start', [(float('nan'), .18), (-1, .18), (1200, 0), (1200, 1)])
def test_invalid_parameters_fail_explicitly(rms, start):
    with pytest.raises(ValueError):
        PlaybackBargeInGuard(rms, start)


def test_playback_deadline_accounts_for_queued_pcm_and_survives_interrupt(monkeypatch):
    async def run():
        async def pending():
            await asyncio.Event().wait()
        monkeypatch.setattr('kufibot_interaction.audio.time.monotonic', lambda: 10.0)
        mic = AlsaMicTrack('pulse:test', barge_in_rms=1200, playback_echo_tail_sec=1.2)
        speaker = AlsaSpeaker(Mock(recv=pending), 'pulse:test', mic)
        try:
            # Two 500 ms chunks queued immediately cannot end after only 350 ms.
            speaker._update_speaking(pcm(2400) * 150)
            speaker._update_speaking(pcm(2400) * 150)
            assert speaker.playback_until == pytest.approx(11.0)
            assert mic.playback_protected_until == pytest.approx(12.2)
            await speaker.interrupt()
            assert not mic.bot_speaking
            assert mic.playback_protected_until == pytest.approx(12.2)
        finally:
            await speaker.close()
            await mic.close_capture()
    asyncio.run(run())


def test_capture_blocks_late_echo_after_speaking_flag_clears(monkeypatch):
    async def run():
        process = Mock(returncode=None, wait=AsyncMock())
        process.stdout = asyncio.StreamReader()
        process.stderr = asyncio.StreamReader()
        process.stderr.feed_eof()
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        now = [10.0]
        monkeypatch.setattr('kufibot_interaction.audio.time.monotonic', lambda: now[0])
        mic = AlsaMicTrack('pulse:test', noise_gate_rms=0, barge_in_rms=1200)
        mic.protect_playback_until(10.5)
        mic.bot_speaking = False
        process.stdout.feed_data(pcm(500) * 18)
        try:
            await mic.start_capture()
            first = await mic.recv()
            assert bytes(first.planes[0]) == bytes(320)
            now[0] = 12.0
            # Old protected frames stay suppressed, then quiet user speech passes.
            process.stdout.feed_data(pcm(90) * 18)
            frames = [await mic.recv() for _ in range(18)]
            assert bytes(frames[-1].planes[0]) == pcm(90)
            assert [frame.pts for frame in [first, *frames]] == list(range(0, 19 * 160, 160))
        finally:
            await mic.close_capture()
    asyncio.run(run())


def test_blocked_bluetooth_write_keeps_playback_protection_active():
    async def run():
        async def pending():
            await asyncio.Event().wait()
        mic = AlsaMicTrack('pulse:test', barge_in_rms=1200, playback_echo_tail_sec=0.1)
        speaker = AlsaSpeaker(Mock(recv=pending), 'pulse:test', mic)
        try:
            speaker._update_speaking(pcm(2400))
            speaker.pending_voice_write = True
            await asyncio.sleep(0.45)
            assert mic.bot_speaking
            old_deadline = mic.playback_protected_until
            speaker.pending_voice_write = False
            await asyncio.sleep(0.1)
            assert not mic.bot_speaking
            assert mic.playback_protected_until > old_deadline
        finally:
            await speaker.close()
            await mic.close_capture()
    asyncio.run(run())


def test_higher_playback_threshold_rejects_echo_but_accepts_interruption():
    guard = PlaybackBargeInGuard(3000)
    echo = feed(guard, [2400] * 100)
    assert echo and all(frame == bytes(320) for frame in echo)
    speech = feed(guard, [5000] * 40 + [0] * 60)
    assert speech.count(pcm(5000)) == 40


def test_loud_listening_audio_cannot_preopen_playback_gate():
    guard = PlaybackBargeInGuard(3000)
    feed(guard, [5000] * 40, protected=False)
    output = feed(guard, [2400] * 60)
    assert output[guard.start_frames - 1:] == [bytes(320)] * (
        len(output) - guard.start_frames + 1)


def test_loud_speech_after_tail_does_not_unmute_buffered_echo():
    guard = PlaybackBargeInGuard(3000)
    feed(guard, [2400] * 40)
    output = feed(guard, [5000] * 40, protected=False)
    assert output[:guard.start_frames - 1] == [bytes(320)] * (guard.start_frames - 1)
    assert output[guard.start_frames - 1:] == [pcm(5000)] * (
        len(output) - guard.start_frames + 1)
