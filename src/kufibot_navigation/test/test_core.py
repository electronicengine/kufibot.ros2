import math
import pytest
from kufibot_navigation.core import Config, Navigator, delta


class Rig:
    def __init__(self, calibrated=True):
        self.now = 10.0
        self.nav = Navigator(Config(calibrated=calibrated, circular_footprint=True), lambda: self.now)
        self.heading = 359.0
        self.distance = 2.0
        self.feed()
        self.nav.tick()
        self.task = self.nav.task('start', 'session', request_id='start', label='mutfak')['task_id']

    def feed(self):
        n = self.nav
        n.set_authority(dict(epoch='epoch', enabled=True, owner=True, provider='verasist', mode='ai'))
        n.set_session('session', True)
        n.applied_mode, n.applied_at = 'ai', self.now
        n.sensor('heading', self.heading)
        n.sensor('range', self.distance)
        n.sensor('range_min', .2)
        head, neck = n.head_target or (90., 60.)
        n.sensor('joints', dict(headLeftRight=head, neck=neck))
        n.sensor('image', 'frame')

    def tick(self):
        self.now += .05
        self.feed()
        return self.nav.tick()

    def request(self, rid, op='observe', **kwargs):
        return self.nav.submit(dict(session_id='session', task_id=self.task, request_id=rid,
            operation=op, observation_id=self.nav.latest_observation, **kwargs))

    def scan(self, rid='scan'):
        assert self.request(rid, sweep_deg=180.) is None
        for _ in range(400):
            assert self.tick() == (0., 0.)
            if self.nav.step is None:
                break
        assert self.nav.results[rid]['status'] == 'ok'
        obs = self.nav.observations[self.nav.latest_observation]
        assert self.nav.task('acknowledge', 'session', self.task, label=obs['id'])['status'] == 'ok'
        return obs


def test_bearing_and_wrap():
    r = Rig()
    assert delta(1, 359) == 2
    assert delta(359, 1) == -2
    assert r.nav.bearing(80) == 9
    assert r.nav.bearing(90, 3) == 2


def test_scan_waits_for_new_image_and_range_after_servo_settles():
    r = Rig()
    assert r.request('one', sweep_deg=0.) is None
    r.tick()
    settled_start = r.now
    for _ in range(5):
        r.now += .05
        r.feed()
        r.nav.sensor('image', 'old', settled_start)
        r.nav.tick()
    assert r.nav.step is not None
    for _ in range(3):
        r.tick()
    obs = r.nav.observations[r.nav.latest_observation]
    assert obs['samples'][0]['image_received_at'] > settled_start + r.nav.c.settle_sec
    assert obs['servo_feedback'] == 'command_estimate'


def test_full_scan_coverage_and_unknown_space():
    r = Rig()
    obs = r.scan()
    assert len(obs['samples']) == 19
    assert len(obs['frames']) == 3
    assert r.nav.turn_clear(obs['samples'])
    assert not r.nav.turn_clear(obs['samples'][1:])
    assert r.nav.corridor_clear(obs['samples'], .3)
    assert not r.nav.corridor_clear(obs['samples'][8:11], .3)


def test_calibration_gate_allows_observation_but_not_motion():
    r = Rig(False)
    r.scan()
    assert r.request('move', 'advance', distance_m=.2)['reason'] == 'calibration_required'


@pytest.mark.parametrize('field,value', [('distance_m', -1), ('distance_m', math.nan),
                                         ('distance_m', .6), ('distance_m', True)])
def test_invalid_distance_never_moves(field, value):
    r = Rig()
    r.scan()
    assert r.request('move', 'advance', **{field: value})['status'] == 'error'
    assert r.tick() == (0., 0.)


def test_delivered_observation_required_and_duplicate_cannot_repeat():
    r = Rig()
    obs = r.scan()
    obs['delivered'] = False
    assert r.request('move', 'advance', distance_m=.2)['reason'] == 'fresh_delivered_observation_required'
    obs['delivered'] = True
    assert r.request('move', 'advance', distance_m=.2) is None
    assert r.request('move', 'advance', distance_m=.2)['reason'] == 'duplicate_request'
    r.nav.revoke('cancelled')
    assert r.request('late', 'advance', distance_m=.2)['status'] == 'error'


def test_advance_progress_and_new_observation():
    r = Rig()
    previous = r.scan()['id']
    assert r.request('move', 'advance', distance_m=.12) is None
    moving = 0
    for _ in range(400):
        drive = r.tick()
        if drive[0]:
            moving += 1
            r.distance -= .01
        if r.nav.step is None:
            break
    result = r.nav.results['move']
    assert moving > 0 and result['status'] == 'ok'
    assert result['moved_m'] >= .12
    assert result['observation_id'] != previous
    assert not r.nav.observations[result['observation_id']]['delivered']


@pytest.mark.parametrize('failure', ['jump', 'heading', 'stale', 'head'])
def test_motion_stops_on_bad_feedback(failure):
    r = Rig()
    r.scan()
    r.request('move', 'advance', distance_m=.2)
    for _ in range(20):
        if r.tick()[0]:
            break
    if failure == 'jump':
        r.distance -= .5
        drive = r.tick()
    elif failure == 'heading':
        r.heading += 8
        drive = r.tick()
    elif failure == 'head':
        r.nav.sensor('joints', dict(headLeftRight=30., neck=60.))
        drive = r.nav.tick()
    else:
        r.nav.sensor('range', None)
        drive = r.nav.tick()
    assert drive == (0., 0.)
    assert r.nav.results['move']['status'] == 'error'


def test_turn_uses_compass_across_north_and_scans_afterwards():
    r = Rig()
    r.scan()
    r.request('turn', 'turn', angle_deg=10.)
    for _ in range(400):
        _, angular = r.tick()
        if angular:
            assert angular < 0  # clockwise compass increase = negative ROS yaw
            r.heading = (r.heading + 1) % 360
        if r.nav.step is None:
            break
    assert r.nav.results['turn']['status'] == 'ok'
    assert 8 <= r.nav.results['turn']['turned_deg'] <= 10


def test_authority_loss_latches_until_new_user_enable_epoch():
    r = Rig()
    r.now += .6
    assert r.nav.tick() == (0., 0.)
    r.feed()
    r.nav.tick()
    assert not r.nav.enabled
    assert not r.nav.task_id
    r.nav.set_authority(dict(r.nav.authority, epoch='new-enable'))
    r.nav.tick()
    assert r.nav.enabled and r.nav.state == 'idle'


def test_llm_timeout_and_session_change_clear_task_and_memory():
    r = Rig()
    r.scan()
    r.now += 61
    r.feed()
    r.nav.tick()
    assert r.nav.reason == 'llm_timeout'
    r.nav.set_session('new-session', True)
    assert not r.nav.route and not r.nav.observations


def test_manual_mode_passes_only_fresh_owner_commands():
    r = Rig()
    n = r.nav
    n.set_authority(dict(mode='remote', epoch='manual', owner=True, enabled=False))
    n.applied_mode = 'remote'
    n.manual, n.manual_at = (.5, 0.), r.now
    assert n.tick() == (.5, 0.)
    r.now += .4
    assert n.tick() == (0., 0.)


def test_obstacle_correction_is_bounded_and_does_not_resume_blind_advance():
    r = Rig()
    r.scan()
    r.request('move', 'advance', distance_m=.2)
    for _ in range(20):
        if r.tick()[0]:
            break
    r.distance = r.nav.stop_distance() - .01
    assert r.tick() == (0., 0.)
    assert r.nav.step['after_scan'] == 'avoid'
    # Obstacle disappears during stopped rescan; at most one small correction.
    r.distance = 2.
    for _ in range(500):
        linear, angular = r.tick()
        assert linear == 0
        if angular:
            r.heading = (r.heading + math.copysign(1, -angular)) % 360
        if r.nav.step is None:
            break
    result = r.nav.results['move']
    assert result['status'] == 'blocked'
    assert abs(result['turned_deg']) <= 15
    assert result['reason'] == 'obstacle_corrected_replan'


def test_no_measured_progress_stops_even_with_fresh_constant_sensors():
    r = Rig()
    r.scan()
    r.request('move', 'advance', distance_m=.2)
    for _ in range(80):
        r.tick()
        if r.nav.step is None:
            break
    assert r.nav.results['move']['reason'] == 'no_measured_progress'
