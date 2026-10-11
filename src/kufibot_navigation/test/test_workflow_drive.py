import pytest
from kufibot_navigation.workflow_drive import WorkflowDrive


def setup_drive():
    now = [10.]
    drive = WorkflowDrive(.25, .5, clock=lambda: now[0])
    authority = {'owner': True, 'mode': 'ai', 'workflow_enabled': True, 'workflow_session_id': 'local', 'epoch': 'epoch'}
    drive.authority_update(authority)
    drive.session = {'session_id': 'local', 'connected': True}
    drive.session_at = now[0]
    return drive, now, authority


def request(**kw):
    return {'operation': 'move', 'request_id': 'r', 'session_id': 'local', 'direction': 'forward', 'speed_mps': .25, 'duration_sec': .2, **kw}


@pytest.mark.parametrize('direction,expected', [('forward', .25), ('backward', -.25)])
def test_direction_duration_and_stop(direction, expected):
    drive, now, _ = setup_drive()
    drive.request(request(direction=direction), 'ai', now[0])
    assert drive.tick('ai', now[0]) == (expected, 0.)
    now[0] += .21
    assert drive.tick('ai', now[0]) == (0., 0.)
    assert drive.results[-1]['status'] == 'ok'
    assert drive.tick('ai', now[0]) is None


@pytest.mark.parametrize('change', ['owner', 'session', 'mode', 'timeout', 'epoch'])
def test_loss_of_authority_stops(change):
    drive, now, authority = setup_drive()
    drive.request(request(duration_sec=5), 'ai', now[0])
    if change == 'owner':
        drive.authority_update({**authority, 'owner': False})
    elif change == 'session':
        drive.session['connected'] = False
    elif change == 'epoch':
        drive.authority_update({**authority, 'epoch': 'new'})
    elif change == 'timeout':
        now[0] += .6
    demand = drive.tick('remote' if change == 'mode' else 'ai', now[0])
    assert demand in (None, (0., 0.))
    assert drive.pending is None
    assert drive.results[-1]['status'] == 'cancelled'


@pytest.mark.parametrize('kwargs', [{'speed_mps': .1}, {'speed_mps': .6}, {'duration_sec': 6}, {'duration_sec': 0}, {'speed_mps': float('nan')}, {'direction': 'left'}, {'duration_sec': True}])
def test_invalid_parameters_never_drive(kwargs):
    drive, now, _ = setup_drive()
    drive.request(request(**kwargs), 'ai', now[0])
    assert drive.pending is None and drive.results[-1]['status'] == 'error'


def test_wrong_session_replay_and_explicit_stop():
    drive, now, _ = setup_drive()
    drive.request(request(session_id='old'), 'ai', now[0])
    assert drive.pending is None
    drive.request(request(), 'ai', now[0])
    drive.request(request(), 'ai', now[0])
    assert drive.results[-1]['reason'] == 'duplicate_request'
    drive.request(request(operation='stop', request_id='stop'), 'ai', now[0])
    assert drive.pending is None


@pytest.mark.parametrize('change', ['unavailable', 'lower_maximum', 'higher_minimum'])
def test_motor_limit_change_stops_active_drive(change):
    drive, now, _ = setup_drive()
    drive.request(request(), 'ai', now[0])
    if change == 'unavailable':
        drive.limits_ready = False
    elif change == 'lower_maximum':
        drive.max_speed = .2
    else:
        drive.min_speed = .3
    assert drive.tick('ai', now[0]) == (0., 0.)
    assert drive.pending is None
    assert drive.results[-1]['status'] == 'error'


def test_missing_motor_limits_reject_new_drive():
    drive, now, _ = setup_drive()
    drive.limits_ready = False
    drive.request(request(), 'ai', now[0])
    assert drive.pending is None
    assert drive.results[-1]['reason'] == 'motor_limits_unavailable'
