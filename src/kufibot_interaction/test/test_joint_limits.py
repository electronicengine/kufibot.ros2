import math

import pytest

from kufibot_interaction.joint_limits import validate_joint_targets


def test_valid_targets_are_preserved():
    angles, clamped = validate_joint_targets(['neck', 'eyeLeft'], [60, 20])
    assert angles == [60.0, 20.0]
    assert clamped == [False, False]


def test_out_of_range_target_is_clamped():
    angles, clamped = validate_joint_targets(['eyeLeft'], [100])
    assert angles == [40.0]
    assert clamped == [True]


@pytest.mark.parametrize('names,angles', [
    (['unknown'], [1]), (['neck'], []), (['neck'], [math.nan])])
def test_invalid_targets_are_rejected(names, angles):
    with pytest.raises(ValueError):
        validate_joint_targets(names, angles)
