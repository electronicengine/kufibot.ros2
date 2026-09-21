import math
import numpy as np
import pytest

from kufibot_interaction.pcm_gain import PcmGain


def test_gain_saturates_without_integer_wraparound():
    gain = PcmGain(20 * math.log10(2))
    pcm = np.array([-20000, -1000, 0, 1000, 20000], dtype='<i2').tobytes()
    assert np.frombuffer(gain(pcm), dtype='<i2').tolist() == [-32768, -2000, 0, 2000, 32767]
    assert (gain.clipped, gain.samples) == (2, 5)


def test_zero_gain_preserves_pcm_exactly():
    pcm = np.array([-32768, -1, 0, 32767], dtype='<i2').tobytes()
    gain = PcmGain()
    assert gain(pcm) == pcm
    assert gain.clipped == 0


@pytest.mark.parametrize('db', [-1, 25, float('nan'), float('inf')])
def test_invalid_gain_rejected(db):
    with pytest.raises(ValueError):
        PcmGain(db)
