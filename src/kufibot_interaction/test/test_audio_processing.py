import asyncio
import ctypes.util
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from kufibot_interaction.audio import AlsaMicTrack
from kufibot_interaction.audio_processing import SpeexDenoiser


def test_missing_denoiser_library_is_explicit(monkeypatch):
    monkeypatch.setattr(ctypes.util, 'find_library', lambda _: None)
    with pytest.raises(RuntimeError, match='libspeexdsp1'):
        SpeexDenoiser()


@pytest.mark.parametrize('level', [-61, 1, float('nan')])
def test_invalid_suppression_is_rejected(level):
    with pytest.raises(ValueError):
        SpeexDenoiser(suppression_db=level)


@pytest.mark.skipif(not ctypes.util.find_library('speexdsp'), reason='SpeexDSP not installed')
def test_native_denoiser_attenuates_stationary_noise():
    denoiser = SpeexDenoiser()
    rng = np.random.default_rng(7)
    original, filtered = [], []
    try:
        for index in range(400):
            samples = rng.normal(0, 1200, 160).astype(np.int16)
            pcm = denoiser.process(samples.tobytes())
            assert len(pcm) == 320
            if index >= 300:
                original.extend(samples.astype(float))
                filtered.extend(np.frombuffer(pcm, dtype=np.int16).astype(float))
        assert np.mean(np.square(filtered)) < np.mean(np.square(original)) * 0.25
        with pytest.raises(ValueError):
            denoiser.process(b'\x00\x00')
    finally:
        denoiser.close()
        denoiser.close()
    with pytest.raises(RuntimeError, match='closed'):
        denoiser.process(bytes(320))


def test_denoised_frames_keep_timestamps_and_full_duplex(monkeypatch):
    async def run():
        denoiser = Mock()
        denoiser.process.return_value = b'\x00\x02' * 160
        factory = Mock(return_value=denoiser)
        monkeypatch.setattr('kufibot_interaction.audio_processing.SpeexDenoiser', factory)
        process = Mock(returncode=None, wait=AsyncMock())
        process.stdout = asyncio.StreamReader()
        process.stdout.feed_data(b'\x00\x10' * 320)
        process.stderr = asyncio.StreamReader()
        process.stderr.feed_eof()
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
        mic = AlsaMicTrack('pulse:test', noise_suppression_db=-25, noise_gate_rms=0)
        mic.bot_speaking = True
        try:
            await mic.start_capture()
            frames = [await mic.recv(), await mic.recv()]
            assert [frame.pts for frame in frames] == [0, 160]
            assert all(bytes(frame.planes[0]) == b'\x00\x02' * 160 for frame in frames)
        finally:
            await mic.close_capture()
        factory.assert_called_once_with(16000, 160, -25)
        denoiser.close.assert_called_once()
    asyncio.run(run())


def test_capture_spawn_failure_releases_denoiser(monkeypatch):
    async def run():
        denoiser = Mock()
        monkeypatch.setattr('kufibot_interaction.audio_processing.SpeexDenoiser', Mock(return_value=denoiser))
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(side_effect=FileNotFoundError))
        mic = AlsaMicTrack('pulse:test', noise_suppression_db=-25)
        with pytest.raises(FileNotFoundError):
            await mic.start_capture()
        denoiser.close.assert_called_once()
        assert mic.readyState == 'ended'
    asyncio.run(run())
