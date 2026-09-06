import math

import pytest

from kufibot_remote.control import Control


@pytest.fixture
def controller():
    now = [100.0]
    control = Control(clock=lambda: now[0])
    owner = object()
    control.command(owner, {'type': 'claim'})
    return control, owner, now


def test_motion_times_out_even_with_heartbeat(controller):
    control, owner, now = controller
    control.command(owner, {'type': 'input', 'drive_y': -1, 'head_x': 1})
    assert control.tick(.05, {'headLeftRight': 90}) == (.25, 0)
    assert control.targets['headLeftRight'] == 87.75
    now[0] += .6
    control.command(owner, {'type': 'heartbeat'})
    assert control.tick(.05, {}) == (0, 0)
    assert control.targets['headLeftRight'] == 87.75


def test_disconnect_holds_remote_and_releases_ownership(controller):
    control, owner, _ = controller
    control.command(owner, {'type': 'input', 'drive_y': -1})
    control.release(object())
    assert control.owner is owner
    control.release(owner)
    assert control.mode == 'remote'
    assert control.owner is None
    assert control.tick(.05, {}) == (0, 0)


def test_owner_lease_expires(controller):
    control, owner, now = controller
    now[0] += 2.1
    control.tick(.05, {})
    assert control.owner is None
    with pytest.raises(ValueError):
        control.command(owner, {'type': 'input'})
    control.command(object(), {'type': 'claim'})


def test_only_one_controller_and_ai_blocks_motion(controller):
    control, owner, _ = controller
    with pytest.raises(ValueError):
        control.command(object(), {'type': 'claim'})
    control.command(owner, {'type': 'mode', 'mode': 'ai'})
    with pytest.raises(ValueError):
        control.command(owner, {'type': 'input', 'head_x': 1})
    assert control.tick(.05, {}) == (0, 0)
    control.command(owner, {'type': 'mode', 'mode': 'remote'})
    assert control.axes['head_x'] == 0


@pytest.mark.parametrize('value', [math.nan, math.inf, '1', True, None, 1.1])
def test_malformed_axes_do_not_change_motion(controller, value):
    control, owner, _ = controller
    with pytest.raises(ValueError):
        control.command(owner, {'type': 'input', 'head_x': value})
    assert control.axes['head_x'] == 0


def test_head_limits_and_feedback_required(controller):
    control, owner, _ = controller
    control.command(owner, {'type': 'input', 'head_x': -1, 'head_y': -1})
    control.tick(.1, {})
    assert control.targets == {}
    control.tick(.1, {'headLeftRight': 179, 'neck': 119})
    assert control.targets == {'headLeftRight': 180, 'neck': 120}
    control.command(owner, {'type': 'joint', 'name': 'leftArm', 'value': 0})
    assert control.targets['leftArm'] == 109


@pytest.mark.parametrize('data', [[], None, 1, {'type': 'unknown'}, {'type': 'mode', 'mode': 'oops'}])
def test_invalid_messages(controller, data):
    control, owner, _ = controller
    with pytest.raises(ValueError):
        control.command(owner, data)
