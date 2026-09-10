import math
import pytest
from kufibot_navigation.core import Config, Navigator, delta


class Rig:
    def __init__(self, calibrated=True):
        self.now = 10.0
        self.nav = Navigator(Config(calibrated=calibrated, circular_footprint=True), lambda: self.now)
        self.heading = 359.0
        self.distance = 2.0
        self.head_joint = 90.0
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
        target, neck = n.head_target or (90., 60.)
        self.head_joint += max(-3.0, min(3.0, target-self.head_joint))
        n.sensor('joints', dict(headLeftRight=self.head_joint, neck=neck))
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
    for _ in range(30):
        r.tick()
        if r.nav.step is None:
            break
    obs = r.nav.observations[r.nav.latest_observation]
    assert obs['frames'][0][0]['image_received_at'] > settled_start + r.nav.c.settle_sec
    assert obs['servo_feedback'] == 'command_estimate'


def test_full_scan_coverage_and_unknown_space():
    r = Rig()
    obs = r.scan()
    assert len(obs['samples']) >= 19
    assert len({sample['received_at'] for sample in obs['samples']}) == len(obs['samples'])
    assert min(sample['relative_deg'] for sample in obs['samples']) <= -90
    assert max(sample['relative_deg'] for sample in obs['samples']) >= 90
    assert len(obs['frames']) == 1
    assert obs['frames'][0][0]['relative_deg'] == 0.0
    assert obs['frames'][0][0]['image_received_at'] > obs['samples'][-1]['image_received_at']
    assert r.nav.turn_clear(obs['samples'])
    assert not r.nav.turn_clear([sample for sample in obs['samples'] if sample['relative_deg'] > -89])
    assert r.nav.corridor_clear(obs['samples'], .3)
    assert not r.nav.corridor_clear(obs['samples'][8:11], .3)


def test_calibration_gate_allows_observation_but_not_motion():
    r = Rig(False)
    r.scan()
    assert r.request('move', 'goto', distance_m=.2)['reason'] == 'calibration_required'


@pytest.mark.parametrize('field,value', [('distance_m', -1), ('distance_m', math.nan),
                                         ('distance_m', 4.0), ('distance_m', True)])
def test_invalid_distance_never_moves(field, value):
    r = Rig()
    r.scan()
    assert r.request('move', 'goto', **{field: value})['status'] == 'error'
    assert r.tick() == (0., 0.)


def test_goto_needs_no_prior_observation_and_duplicate_cannot_repeat():
    r = Rig()
    assert r.request('move', 'goto', distance_m=.2) is None
    assert r.request('move', 'goto', distance_m=.2)['reason'] == 'duplicate_request'
    r.nav.revoke('cancelled')
    assert r.request('late', 'goto', distance_m=.2)['status'] == 'error'


def test_goto_requires_some_motion_and_respects_turn_limit():
    r = Rig()
    assert r.request('none', 'goto')['reason'] == 'no_motion_requested'
    assert r.request('bigturn', 'goto', angle_deg=181.)['reason'] == 'angle_limit'


def test_goto_advance_progress_returns_one_final_observation():
    r = Rig()
    previous = r.scan()['id']
    assert r.request('move', 'goto', distance_m=.12) is None
    moving = 0
    for _ in range(1500):
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
    final = r.nav.observations[result['observation_id']]
    assert len(final['samples']) == 1
    assert final['map']['robot_pose'][1] >= .12


def test_later_sensor_read_updates_one_world_fixed_boundary_map():
    r = Rig()
    first = r.scan()
    assert first['map']['origin'] == [0.0, 0.0]
    assert len(first['map']['obstacle_points']) > 19
    assert r.request('move', 'goto', distance_m=.12) is None
    for _ in range(1500):
        if r.tick()[0]:
            r.distance -= .01
        if r.nav.step is None:
            break
    final = r.nav.observations[r.nav.latest_observation]
    # The robot advanced toward the same flat front boundary. Its measured
    # travel offsets the new range endpoint onto the existing 2 m map cell.
    assert len(final['map']['obstacle_points']) == len(first['map']['obstacle_points'])
    second = r.scan('after-move')
    assert second['map']['robot_pose'][1] >= .12
    assert len(second['map']['obstacle_points']) >= len(first['map']['obstacle_points'])
    assert len({tuple(point) for point in second['map']['obstacle_points']}) == len(
        second['map']['obstacle_points'])
    assert set(map(tuple, first['map']['obstacle_points'])) <= set(
        map(tuple, second['map']['obstacle_points']))


@pytest.mark.parametrize('failure', ['jump', 'heading', 'stale', 'head'])
def test_motion_stops_on_bad_feedback(failure):
    r = Rig()
    r.scan()
    r.request('move', 'goto', distance_m=.2)
    for _ in range(1500):
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
    r.request('turn', 'goto', angle_deg=10.)
    bursted = False
    for _ in range(1500):
        _, angular = r.tick()
        if angular:
            bursted = True
            assert angular < 0  # clockwise compass increase = negative ROS yaw
            target = r.nav.step['target_heading']
            r.heading = (r.heading + delta(target, r.heading) * .9) % 360
        if r.nav.step is None:
            break
    assert bursted
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


def test_goto_stops_immediately_when_an_obstacle_appears():
    r = Rig()
    r.scan()
    r.request('move', 'goto', distance_m=.2)
    for _ in range(2000):
        if r.tick()[0]:
            break
    r.distance = r.nav.stop_distance() - .01
    assert r.tick() == (0., 0.)
    for _ in range(100):
        r.tick()
        if r.nav.step is None:
            break
    result = r.nav.results['move']
    assert result['status'] == 'blocked'
    assert result['reason'] == 'obstacle'


def test_goto_does_not_scan_or_correct_after_an_obstacle():
    r = Rig()
    r.scan()
    r.request('move', 'goto', distance_m=.2)
    for _ in range(2000):
        if r.tick()[0]:
            break
    r.distance = r.nav.stop_distance() - .01
    assert r.tick() == (0., 0.)
    for _ in range(100):
        r.tick()
        if r.nav.step is None:
            break
    result = r.nav.results['move']
    assert result['status'] == 'blocked'
    assert result['reason'] == 'obstacle'
    assert result['moved_m'] < .2


def test_no_measured_progress_stops_even_with_fresh_constant_sensors():
    r = Rig()
    r.scan()
    r.request('move', 'goto', distance_m=.2)
    for _ in range(2000):
        r.tick()
        if r.nav.step is None:
            break
    assert r.nav.results['move']['reason'] == 'no_measured_progress'


def test_revoked_initial_scan_explains_authority_loss_and_rejects_old_task():
    r = Rig()
    assert r.request('initial', sweep_deg=180.) is None
    r.nav.set_authority(dict(epoch='stopped', enabled=False, owner=True,
                             provider='verasist', mode='ai', reason='stop_requested'))
    result = r.nav.results['initial']
    assert result['status'] == 'cancelled'
    assert result['reason'] == 'authority_changed'
    assert result['authority_reason'] == 'stop_requested'
    assert r.nav.drive == (0., 0.)
    assert r.nav.task_id == ''
    status = r.nav.task('status', 'session', r.task)
    assert status['reason'] == 'not_authorized'
    assert status['authority_reason'] == 'stop_requested'



def test_guidance_uses_real_corridor_gate_and_keeps_unknown_space_blocked():
    r = Rig()
    obs = r.scan()
    g = obs['guidance']
    assert g['front_sensor_distance_cm'] == 200
    assert g['front_body_gap_estimate_cm'] == 180
    assert g['largest_checked_forward_step_cm'] == 150.0
    assert r.nav.corridor_clear(obs['samples'], g['largest_checked_forward_step_cm'] / 100)
    assert r.nav.observation_guidance([obs['samples'][9]])['largest_checked_forward_step_cm'] == 0
    r.nav.c.calibrated = False
    assert r.nav.observation_guidance(obs['samples'])['largest_checked_forward_step_cm'] == 0


def test_front_camera_waits_for_new_frame_after_scan_and_uses_camera_offset():
    r = Rig()
    r.nav.c.camera_yaw_offset_deg = 10
    assert r.request('single', sweep_deg=0.) is None
    for _ in range(30):
        r.tick()
        if r.nav.step and r.nav.step['phase'] == 'scan_front':
            break
    assert r.nav.step['phase'] == 'scan_front'
    old_stamp = r.now
    for _ in range(15):
        r.now += .05
        r.feed()
        r.nav.sensor('image', 'old', old_stamp)
        r.nav.tick()
    assert not r.nav.latest_observation
    for _ in range(10):
        r.tick()
        if r.nav.step is None:
            break
    obs = r.nav.observations[r.nav.latest_observation]
    assert len(obs['frames']) == 1
    meta, _ = obs['frames'][0]
    assert abs(delta(meta['camera_bearing_deg'], r.heading)) < .01
    assert meta['image_received_at'] > old_stamp


def corridor_samples(width, front=4.):
    """Ray distances to parallel corridor walls and an end wall."""
    return [dict(relative_deg=angle, range_m=min(
        front / max(1e-12, math.cos(math.radians(angle))),
        width / 2 / max(1e-12, abs(math.sin(math.radians(angle))))))
        for angle in range(-90, 91, 10)]


def test_open_corridor_side_walls_do_not_block_forward_motion():
    r = Rig()
    samples = corridor_samples(1.)
    for distance in (.1, .3, .6, 1., 2.):
        assert r.nav.corridor_clear(samples, distance), distance


def test_circular_turn_uses_footprint_clearance_without_forward_braking_travel():
    r = Rig()
    samples = corridor_samples(.8, front=.5)
    assert r.nav.turn_clear(samples)
    assert not r.nav.turn_clear(corridor_samples(.5, front=.5))
    assert not r.nav.turn_clear(samples[1:])
    r.nav.c.circular_footprint = False
    assert not r.nav.turn_clear(samples)


def test_turn_offset_expands_required_clearance():
    r = Rig()
    r.nav.c.lidar_forward_offset_m = .15
    assert not r.nav.turn_clear(corridor_samples(.8, front=.5))


@pytest.mark.parametrize('distance,blocked', [(.29, True), (.4, False)])
def test_turn_monitors_actual_footprint_clearance_while_moving(distance, blocked):
    r = Rig()
    r.request('turn', 'goto', angle_deg=90.)
    for _ in range(500):
        if r.tick()[1]:
            break
    else:
        pytest.fail('Turn never started')
    r.distance = distance
    drive = r.tick()
    if blocked:
        assert drive == (0., 0.)
        for _ in range(100):
            r.tick()
            if r.nav.step is None:
                break
        assert r.nav.results['turn']['reason'] == 'obstacle'
    else:
        assert drive[1] != 0
        assert r.nav.step is not None


def test_corridor_rejects_narrow_gap_front_obstacle_and_missing_side_coverage():
    r = Rig()
    assert not r.nav.corridor_clear(corridor_samples(.5), .5)
    assert not r.nav.corridor_clear(corridor_samples(1., front=.8), .5)
    assert not r.nav.corridor_clear(corridor_samples(1.)[1:-1], .5)
    samples = corridor_samples(1.)
    samples[12]['range_m'] = .4  # A near obstacle intrudes from the right.
    assert not r.nav.corridor_clear(samples, .5)
