"""Regression tests for disappearing walls and moving-frame map distortion."""
import math
import random

import pytest

from kufibot_navigation.boundary_map import boundary_paths
from kufibot_navigation.core import Config, Navigator
from kufibot_navigation.map_geometry import SensorHistory, sensor_origin
from kufibot_navigation.metric_map import MetricMap


def test_later_crossing_and_no_return_rays_never_erase_a_wall():
    m = MetricMap()
    for x in range(-10, 11):
        m.ray([x*.1, 0.], 0., 2.)
    original = dict(m.occupied)
    outline = m.snapshot([0., 0.], 0., 'estimated')['boundary_paths']
    for i in range(100):
        m.ray([0., 0.], (i-50)*.1, 3., hit=False)
    assert m.occupied == original
    assert m.snapshot([0., 0.], 0., 'estimated')['boundary_paths'] == outline
    assert not set(m.free) & set(m.occupied)
    assert (0, 25) not in m.free  # conflicting ray cannot clear behind a known wall


def test_capacity_does_not_evict_old_walls_or_report_rejected_obstacle_as_free():
    m = MetricMap()
    m.max_cells = 3
    for x in range(3):
        m.ray([x, 0.], 0., 1.)
    original = dict(m.occupied)
    m.ray([4., 0.], 0., 1.)
    assert m.occupied == original
    assert (40, 10) not in m.free
    assert m.snapshot([0., 0.], 0., 'estimated')['occupied_capacity_reached']


def test_small_range_noise_does_not_thicken_or_remove_an_existing_wall():
    m = MetricMap()
    m.ray([0., 0.], 0., 2.)
    for distance in [2.04, 2.051, 1.949, 1.98, 2.06]:
        m.ray([0., 0.], 0., distance)
    assert list(m.occupied.values()) == [[0., 2.]]


@pytest.mark.parametrize('origin,bearing,distance', [([math.nan, 0], 0, 1),
    ([0, 0], math.inf, 1), ([0, 0], 0, -1), ([0, 0], 0, True)])
def test_invalid_rays_cannot_corrupt_existing_geometry(origin, bearing, distance):
    m = MetricMap()
    m.ray([0., 0.], 0., 1.)
    old = m.revision
    assert m.ray(origin, bearing, distance) is False
    assert m.revision == old and list(m.occupied.values()) == [[0., 1.]]


def test_delayed_rays_do_not_rewind_measurement_time():
    m = MetricMap(lambda: 20.)
    m.ray([0., 0.], 0., 1., measured_at=15.)
    assert not m.ray([0., 0.], 90., 1., measured_at=14.)
    assert m.measured_at == 15.


def test_distant_cell_cannot_make_existing_boundaries_disappear():
    wall = [[x*.1, 2.] for x in range(12)]
    original = boundary_paths(wall)
    extended = boundary_paths(wall+[[1000., 1000.]])
    assert extended == original
    other_wall = [[1000.+x*.1, 1000.] for x in range(12)]
    assert len(boundary_paths(wall+other_wall)) == 2


def test_grid_outlines_preserve_holes_and_doorways():
    ring = [[x*.1, y*.1] for x in range(3) for y in range(3) if (x, y) != (1, 1)]
    assert len(boundary_paths(ring)) == 2
    doorway = [[x*.1, 0.] for x in range(30) if not 10 <= x < 20]
    assert len(boundary_paths(doorway)) == 2
    assert boundary_paths([[0, 0], [.1, .1]]) == []
    assert all(path[0] == path[-1] for path in boundary_paths(doorway))


def test_random_cell_outlines_are_closed_and_have_no_long_edges_across_gaps():
    rng = random.Random(13)
    for _ in range(100):
        points = [[x*.1, y*.1] for x in range(10) for y in range(10) if rng.random() < .4]
        for path in boundary_paths(points):
            assert path[0] == path[-1]
            assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(path, path[1:]))


def test_time_alignment_interpolates_through_north_and_rejects_stale_angles():
    h = SensorHistory()
    h.add('heading', 1., 359.)
    h.add('heading', 1.1, 1.)
    assert h.at('heading', 1.05, .1) == pytest.approx(0.)
    h.add('joints', 1., {'headLeftRight': 60., 'neck': 60.})
    h.add('joints', 1.1, {'headLeftRight': 80., 'neck': 60.})
    assert h.at('joints', 1.05, .1)['headLeftRight'] == pytest.approx(70.)
    assert h.at('heading', 1.4, .1) is None
    h.add('heading', 1.05, 90.)  # out-of-order packet
    assert h.at('heading', 1.1, .1) == 1.


def nav_at_start():
    n = Navigator(Config(calibrated=True), lambda: 10.)
    n.sensor('heading', 0.)
    n.sensor('joints', dict(headLeftRight=90., neck=60.))
    n.sensor('range', 2.)
    n.record_live_map_point()
    return n


def test_progress_is_integrated_per_leg_not_rotated_by_the_latest_compass():
    n = nav_at_start()
    step = dict(operation='goto', moved_m=1., origin_heading=0.)
    n.step = step
    assert n.current_pose() == pytest.approx([0., 1.])
    n.sensor('heading', 90.)
    assert n.current_pose() == pytest.approx([0., 1.])
    step.update(origin_heading=90., moved_m=1.5)
    assert n.current_pose() == pytest.approx([.5, 1.])
    n.update_map_pose(step)
    n.update_map_pose(step)
    assert n.map_pose == pytest.approx([.5, 1.])
    assert n.current_pose() == pytest.approx([.5, 1.])


def test_sensor_offset_rotates_with_body_but_existing_wall_stays_world_fixed():
    assert sensor_origin([1., 2.], 90., .2, .1) == pytest.approx([1.2, 1.9])
    n = nav_at_start()
    n.c.lidar_forward_offset_m = .2
    n.c.lidar_lateral_offset_m = .1
    n.map_pose = [1., 1.]
    n.sensor('heading', 90.)
    n.sensor('joints', dict(headLeftRight=0., neck=60.))  # head looks south
    n.sensor('range', 1., stamp=10.01)
    n.clock = lambda: 10.02
    n.record_live_map_point()
    assert (0, 20) in n.metric_map.occupied
    assert n.metric_map.key(1.2, -.1) in n.metric_map.occupied


def test_stale_head_angle_cannot_smear_a_map_with_a_new_range():
    now = [10.]
    n = Navigator(clock=lambda: now[0])
    n.sensor('heading', 0.)
    n.sensor('joints', dict(headLeftRight=90., neck=60.))
    now[0] = 10.2
    n.sensor('heading', 0.)
    n.sensor('range', 2.)
    n.record_live_map_point()
    assert not n.metric_map.occupied
    assert n.map_skip_reason == 'sensor_time_mismatch'


def test_late_simulator_source_preserves_map_id_geometry_and_startup_offset():
    n = nav_at_start()
    mid = n.metric_map.id
    n.map_pose = [1., 2.]
    n.sensor('heading', 90.)
    def sample(x):
        n.update_simulation(dict(pose=dict(x=x, y=20., theta_deg=90.),
            lidar_pose=dict(x=x, y=20., bearing_deg=90.), lidar_range_m=1., lidar_hit=True))
    sample(10.)
    assert n.metric_map.id == mid and (0, 20) in n.metric_map.occupied
    assert n.map_pose == pytest.approx([1., 2.])
    sample(10.5)
    assert n.map_pose == pytest.approx([1.5, 2.])
    assert n.map_snapshot()['robot_heading_deg'] == 90.


def test_raw_time_aligned_compass_is_used_for_map_not_lagged_control_filter():
    n = nav_at_start()
    n.sensor('heading', 45., stamp=10.01, mapping_value=90.)
    n.sensor('joints', dict(headLeftRight=90., neck=60.), stamp=10.01)
    n.sensor('range', 1., stamp=10.01)
    n.clock = lambda: 10.02
    n.record_live_map_point()
    assert n.metric_map.key(1., 0.) in n.metric_map.occupied
    assert n.body_heading() == 45.


def test_observing_same_wall_after_forward_travel_keeps_its_world_coordinates():
    now = [10.]
    n = Navigator(Config(calibrated=True), lambda: now[0])
    def scan():
        for angle in range(-45, 46, 5):
            now[0] += .02
            n.sensor('heading', 0.)
            n.sensor('joints', dict(headLeftRight=90.-angle, neck=60.))
            distance = (2.-n.current_pose()[1])/math.cos(math.radians(angle))
            n.sensor('range', distance)
            n.record_live_map_point()
    scan()
    first = set(n.metric_map.occupied)
    n.step = dict(operation='goto', moved_m=.5, origin_heading=0.)
    n.update_map_pose(n.step)
    n.step = None
    scan()
    assert first <= set(n.metric_map.occupied)
    assert all(y == 20 for x, y in n.metric_map.occupied)
    assert n.current_pose() == pytest.approx([0., .5])


def test_manual_motion_is_integrated_before_its_lidar_ray_and_only_once():
    now = [10.]
    n = Navigator(Config(calibrated=True), lambda: now[0])
    def feed(distance):
        n.set_authority(dict(epoch='e', owner=True, enabled=False, mode='remote', provider='verasist'))
        n.applied_mode, n.applied_at = 'remote', now[0]
        n.manual, n.manual_at = (.25, 0.), now[0]
        n.sensor('heading', 0.)
        n.sensor('joints', dict(headLeftRight=90., neck=60.))
        n.sensor('range', distance)
    feed(2.)
    n.tick()
    assert n.map_pose == [0., 0.]  # command starts now, not in the previous interval
    for i in range(1, 21):
        now[0] += .05
        feed(2.-.0125*i)
        n.tick()
    assert n.map_pose == pytest.approx([0., .25])
    assert list(n.metric_map.occupied.values()) == [[0., 2.]]


def test_only_occupied_changes_rebuild_wall_outlines(monkeypatch):
    import kufibot_navigation.metric_map as module
    calls = []
    original = module.boundary_paths
    def track(points):
        calls.append(points)
        return original(points)
    monkeypatch.setattr(module, 'boundary_paths', track)
    m = MetricMap()
    m.ray([0., 0.], 0., 2.)
    m.snapshot([0., 0.], 0., 'test')
    m.ray([0., 0.], 90., 2., hit=False)
    m.snapshot([0., 0.], 0., 'test')
    assert len(calls) == 1


def test_optional_servo_fields_do_not_break_lidar_head_interpolation():
    h = SensorHistory()
    h.add('joints', 1., dict(headLeftRight=60., neck=60., leftArm=90.))
    h.add('joints', 1.1, dict(headLeftRight=80., neck=60.))
    assert h.at('joints', 1.05, .1)['headLeftRight'] == pytest.approx(70.)


def test_repeated_noisy_scans_from_moved_robot_preserve_established_wall():
    m = MetricMap()
    rng = random.Random(42)
    for angle in range(-45, 46):
        m.ray([0., 0.], angle, 2./math.cos(math.radians(angle)))
    established = set(m.occupied)
    for _ in range(5):
        for angle in range(-45, 46):
            distance = 1.5/math.cos(math.radians(angle))+rng.gauss(0., .02)
            m.ray([0., .5], angle, distance)
    assert established <= set(m.occupied)
    assert all(abs(y-20) <= 1 for x, y in m.occupied)
