"""Deterministic whole-route admission, execution, recovery and map tests."""
import math

import pytest

from kufibot_navigation.core import Navigator, Config
from kufibot_navigation.metric_map import MetricMap


class RouteRig:
    def __init__(self):
        self.now = 10.
        self.nav = Navigator(Config(calibrated=True, circular_footprint=True), lambda: self.now)
        self.head = 90.
        self.heading = 0.
        self.distance = 6.
        self.feed()
        self.nav.tick()
        self.task = self.nav.task('start', 's', request_id='start')['task_id']
        self.fill_free()

    def fill_free(self):
        m = self.nav.metric_map
        for x in range(-15, 101):
            for y in range(-15, 101):
                m.free[x, y] = m.point((x, y))
        m.revision += 1
        m.measured_at = self.now

    def feed(self):
        n = self.nav
        n.set_authority(dict(epoch='e', owner=True, enabled=True, mode='ai', provider='verasist'))
        n.set_session('s', True)
        n.applied_mode, n.applied_at = 'ai', self.now
        target, neck = n.head_target or (90., 60.)
        self.head += max(-3., min(3., target-self.head))
        n.sensor('heading', self.heading)
        n.sensor('joints', dict(headLeftRight=self.head, neck=neck))
        n.sensor('range', self.distance)
        n.sensor('image', 'image')

    def submit(self, points, **overrides):
        req = dict(session_id='s', task_id=self.task, request_id='route',
                   map_id=self.nav.metric_map.id, map_revision=self.nav.metric_map.revision,
                   waypoints=[dict(x_m=x, y_m=y) for x, y in points])
        req.update(overrides)
        return self.nav.submit_route(req)

    def tick(self):
        self.now += .01
        self.feed()
        drive = self.nav.tick()
        self.heading = (self.heading-math.degrees(drive[1]*.01)) % 360.
        self.distance -= drive[0]*.01
        return drive

    def finish(self, limit=6000):
        for _ in range(limit):
            self.tick()
            if not self.nav.route_request:
                return self.nav.results['route']
        pytest.fail('route did not settle')


def test_numeric_map_rays_leave_unknown_gaps_and_do_not_invent_clipped_walls():
    m = MetricMap(lambda: 10.)
    m.ray([0., 0.], 0., 1.)
    assert (0, 10) in m.occupied and (0, 4) in m.free
    assert (4, 4) not in m.free
    m.ray([0., 0.], 90., 20.)
    assert (80, 0) in m.free and (80, 0) not in m.occupied
    snap = m.snapshot([0., 0.], 90., 'estimated')
    assert snap['map_id'] and snap['revision'] > 0
    assert snap['frame'] == 'startup_robot_pose' and snap['resolution_m'] == .1
    assert 'not complete room walls' in snap['boundary_meaning']


@pytest.mark.parametrize('overrides,reason', [
    ({'map_id': 'other'}, 'invalid_map'), ({'map_revision': True}, 'invalid_map'),
    ({'map_revision': 9999}, 'invalid_map'), ({'waypoints': []}, 'invalid_waypoints'),
    ({'waypoints': [{'x_m': float('nan'), 'y_m': 1}]}, 'invalid_waypoints'),
    ({'waypoints': [{'x_m': True, 'y_m': 1}]}, 'invalid_waypoints'),
    ({'waypoints': [{'x_m': 0, 'y_m': 1}]*65}, 'invalid_waypoints')])
def test_invalid_route_never_starts(overrides, reason):
    r = RouteRig()
    assert r.submit([(0, 1)], **overrides)['reason'] == reason
    assert r.tick() == (0., 0.)
    assert r.nav.route_plan is None


def test_entire_route_validated_before_first_movement_including_unknown_and_clearance():
    r = RouteRig()
    r.nav.metric_map.free.pop((10, 10))
    assert r.submit([(0, .5), (1, 1)])['reason'] == 'route_not_measured_clear'
    assert r.nav.step is None and r.nav.route_plan is None
    r.fill_free()
    # Center line is clear; occupied cell lies inside the robot's swept width.
    r.nav.metric_map.occupied[(2, 5)] = [.2, .5]
    assert r.submit([(0, 1)])['reason'] == 'route_not_measured_clear'


def test_route_uses_all_waypoints_and_captures_only_one_final_full_scan():
    r = RouteRig()
    assert r.submit([(0., .5), (.5, .5)]) is None
    assert r.nav.route_plan['waypoints'] == [{'x_m': 0., 'y_m': .5}, {'x_m': .5, 'y_m': .5}]
    assert r.nav.step is None and r.nav.drive == (0., 0.)  # visible before motion
    result = r.finish()
    assert result['status'] == 'ok', result
    assert result['route_plan']['completed_count'] == 2
    assert result['route_plan']['status'] == 'completed'
    assert len(r.nav.observations) == 1
    assert len(r.nav.observations[result['observation_id']]['samples']) > 19
    assert math.dist(result['robot_pose'], [.5, .5]) <= .15
    assert r.nav.route_plan is not None  # retained after completion


def test_long_segment_is_split_without_model_calls():
    r = RouteRig()
    r.nav.c.max_goto_distance_m = .3
    assert r.submit([(0., .8)]) is None
    result = r.finish()
    assert result['status'] == 'ok', result
    moves = [v for v in r.nav.results.values() if v.get('moved_m', 0) > 0]
    assert len(moves) >= 2
    assert all(v['moved_m'] <= .325 for v in moves)


@pytest.mark.parametrize('failure', ['obstacle', 'map', 'stop', 'disconnect', 'stale'])
def test_route_stops_without_advancing_remaining_points(failure):
    r = RouteRig()
    assert r.submit([(0., .6), (0., 1.2)]) is None
    for _ in range(300):
        if r.tick()[0]:
            break
    else:
        pytest.fail('did not start')
    if failure == 'obstacle':
        r.distance = .1
    elif failure == 'map':
        r.nav.metric_map.occupied[(0, 10)] = [0., 1.]
        r.nav.metric_map.revision += 1
    elif failure == 'stop':
        r.nav.cancel_route()
    elif failure == 'disconnect':
        r.nav.set_session('s', False)
    else:
        r.now += 1.
        r.nav.tick()
    if r.nav.route_request:
        result = r.finish()
    else:
        result = r.nav.results['route']
    assert result['status'] != 'ok'
    assert result['route_plan']['completed_count'] == 0
    assert r.nav.drive == (0., 0.)
    if failure in ('obstacle', 'map'):
        assert result.get('observation_id')
    if failure == 'disconnect':
        assert r.nav.route_plan is None


def test_task_changes_keep_map_and_new_route_replaces_previous():
    r = RouteRig()
    mid = r.nav.metric_map.id
    assert r.submit([(0, .2)], map_revision=0) is None
    assert r.finish()['status'] == 'ok'
    assert r.submit([(0, .4)], request_id='replacement') is None
    assert r.nav.route_plan['route_id'] == 'replacement'
    r.nav.cancel_route()
    r.nav.task('finish', 's', r.task)
    r.nav.task('start', 's', request_id='start2')
    assert r.nav.metric_map.id == mid
    r.nav.set_session('other', True)
    assert r.nav.route_plan is None and r.nav.metric_map.id == mid


def test_simulator_pose_and_rotated_anchor_match_numeric_map():
    r = RouteRig()
    r.nav.update_simulation(dict(pose=dict(x=1., y=2., theta_deg=90.),
        lidar_pose=dict(x=1., y=2., bearing_deg=90.), lidar_range_m=1., lidar_hit=True))
    mid = r.nav.metric_map.id
    r.nav.update_simulation(dict(pose=dict(x=1.5, y=2., theta_deg=90.),
        lidar_pose=dict(x=1.5, y=2., bearing_deg=90.), lidar_range_m=.5, lidar_hit=True))
    assert r.nav.map_pose == pytest.approx([0., .5])
    assert r.nav.metric_map.id == mid
    assert [0., 1.] in r.nav.metric_map.occupied.values()
    assert [0., 6.] in r.nav.metric_map.occupied.values()  # pre-existing wall survives source attachment
    assert r.nav.map_snapshot()['pose_source'] == 'simulation'


def test_compact_free_cells_are_lossless_and_smaller_for_room_rows():
    r = RouteRig()
    snapshot = r.nav.map_snapshot()
    compact = MetricMap.compact(snapshot)
    decoded = {(x, y) for y, first, last in compact['free_cell_runs'] for x in range(first, last+1)}
    original = {r.nav.metric_map.key(*point) for point in snapshot['free_cells']}
    assert decoded == original
    assert len(compact['free_cell_runs']) < len(original)/10
    assert 'free_cells' in snapshot and 'free_cells' not in compact
    assert compact['map_id'] == snapshot['map_id']


def test_exploration_route_scans_then_allows_a_new_complete_route():
    r = RouteRig()
    m = r.nav.metric_map
    m.free.clear()
    m.occupied.clear()
    for angle in range(360):
        m.ray([0., 0.], angle, 1., hit=False)
    assert r.submit([(0., 1.2)])['reason'] == 'route_not_measured_clear'
    assert r.submit([(0., .3)]) is None
    first = r.finish()
    assert first['status'] == 'ok', first
    observation = r.nav.observations[first['observation_id']]
    assert observation['map']['revision'] > first['route_plan']['map_revision']
    assert r.submit([(0., .7), (0., 1.2)], request_id='explored') is None
    assert len(r.nav.route_plan['waypoints']) == 2
    r.nav.cancel_route()


def test_future_occupied_cell_invalidates_an_older_revision_route():
    r = RouteRig()
    old = r.nav.metric_map.revision
    r.nav.metric_map.occupied[(0, 9)] = [0., .9]
    r.nav.metric_map.revision += 1
    assert r.submit([(0, 1)], map_revision=old)['reason'] == 'route_not_measured_clear'


def test_route_requires_calibration_and_respects_configured_turn_bound():
    r = RouteRig()
    r.nav.c.calibrated = False
    assert r.submit([(.5, 0)])['reason'] == 'calibration_required'
    r.nav.c.calibrated = True
    r.nav.c.max_goto_turn_deg = 45.
    assert r.submit([(.5, 0)]) is None
    r.tick()
    assert r.nav.step['distance_m'] == 0.
    assert r.nav.step['angle_deg'] == 45.
    assert r.finish()['status'] == 'ok'


def test_failed_final_scan_does_not_return_a_previous_image_as_fresh():
    r = RouteRig()
    r.nav.latest_observation = 'old-photo'
    assert r.submit([(0., .2)]) is None
    for _ in range(1000):
        r.tick()
        if r.nav.step and r.nav.step['operation'] == 'observe':
            break
    else:
        pytest.fail('final scan not started')
    r.nav.sensor('image', None)
    r.nav.tick()
    result = r.nav.results['route']
    assert result['status'] == 'error'
    assert 'observation_id' not in result


def test_session_heartbeat_loss_clears_route_and_calibration_loss_stops_it():
    r = RouteRig()
    assert r.submit([(0., .5)]) is None
    r.nav.c.calibrated = False
    assert r.tick() == (0., 0.)
    assert r.nav.route_plan['reason'] == 'calibration_required'
    r.now += 1.
    r.nav.tick()
    assert r.nav.route_plan is None
