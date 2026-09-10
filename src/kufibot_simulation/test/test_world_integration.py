"""World pose integration, lidar/compass publishing and wall collision.

Uses real DriveCommand/JointState-style callbacks; dt is controlled by
back-dating last_tick_at, matching the monotonic-time trick already used in
test_motor_profile.py, so no real sleeping is required.
"""
import math
import time

import pytest
import rclpy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState

from kufibot_simulation.world_node import WorldNode


@pytest.fixture
def node():
    rclpy.init()
    n = WorldNode()
    n.ranges, n.headings = [], []
    n.range_pub.publish = lambda msg: n.ranges.append(msg.range)
    n.heading_pub.publish = lambda msg: n.headings.append(msg.data)
    yield n
    n.destroy_node()
    rclpy.shutdown()


def twist(linear=0.0, angular=0.0):
    t = Twist()
    t.linear.x, t.angular.z = linear, angular
    return t


def test_forward_motion_moves_the_robot_along_its_heading(node):
    node.x, node.y, node.theta = 0.6, 0.6, 0.0
    node._applied_twist(twist(linear=0.1))
    node.last_tick_at = time.monotonic() - 1.0
    node._tick()
    assert node.y > 0.6 and math.isclose(node.x, 0.6, abs_tol=1e-6)


def test_turning_changes_heading_without_moving(node):
    node.x, node.y, node.theta = 0.6, 0.6, 0.0
    node._applied_twist(twist(angular=0.5))
    node.last_tick_at = time.monotonic() - 1.0
    node._tick()
    assert node.theta == pytest.approx((360 - math.degrees(.5)) % 360, abs=.1)
    assert math.isclose(node.x, 0.6, abs_tol=1e-6)
    assert math.isclose(node.y, 0.6, abs_tol=1e-6)


def test_motion_stops_at_a_wall(node):
    node.x, node.y, node.theta = 3.0, 2.0, 90.0  # facing east toward the wall at x=4
    node._applied_twist(twist(linear=1.0))
    for _ in range(50):
        node.last_tick_at = time.monotonic() - 0.05
        node._tick()
    assert node.x < 4.0 - node.body_radius + 1e-6


def test_stale_applied_twist_is_treated_as_stopped(node):
    node._applied_twist(twist(linear=0.5))
    node.last_twist_at = time.monotonic() - 10.0
    node.last_tick_at = time.monotonic() - 1.0
    node._tick()
    assert node.linear == 0.0 and node.angular == 0.0


def test_lidar_and_compass_reflect_the_integrated_pose(node):
    node.x, node.y, node.theta = 2.0, 0.6, 0.0
    node.last_tick_at = time.monotonic()
    node._tick()
    assert node.headings[-1] == pytest.approx(0.0)
    assert node.ranges[-1] > 0


def test_sensor_noise_is_bounded_quantized_and_seeded(node):
    node.lidar_noise_stddev = .02
    node.lidar_quantization = .005
    node.compass_noise_stddev = .8
    node.noise.seed(42)
    readings = [node._noisy_lidar(2.0) for _ in range(4)]
    headings = [node._noisy_heading() for _ in range(4)]
    assert any(value != 2.0 for value in readings)
    assert all(node.lidar_min_range <= value <= node.lidar_max_range for value in readings)
    assert all((value / .005).is_integer() for value in readings)
    assert any(value != node.theta for value in headings)
    node.noise.seed(42)
    assert readings == [node._noisy_lidar(2.0) for _ in range(4)]


def test_head_yaw_from_joint_states_changes_the_look_bearing(node):
    node.theta = 0.0
    node._joint_states(JointState(name=['headLeftRight'], position=[math.radians(45.0)]))
    assert node.head_deg == pytest.approx(45.0)
    assert node._look_bearing(0.0) == pytest.approx(45.0)


def test_camera_and_lidar_use_the_drawn_eye_centres(node):
    node.x, node.y, node.theta = 1.0, 2.0, 0.0
    lidar = node._sensor_pose(node.lidar_lateral_offset, node.lidar_forward_offset, 0.0)
    camera = node._sensor_pose(node.camera_lateral_offset, node.camera_forward_offset, 0.0)
    assert lidar[:2] == pytest.approx((1.03, 2.0))
    assert camera[:2] == pytest.approx((.97, 2.0))
    assert math.dist(lidar[:2], camera[:2]) == pytest.approx(.06)
    assert node.sensor_height == pytest.approx(.285)
    assert node.body_radius == pytest.approx(.16)


def test_neck_bottom_is_level_and_only_looks_up(node):
    node._joint_states(JointState(name=['neck'], position=[0.0]))
    assert node.neck_deg == 0.0
    node._joint_states(JointState(name=['neck'], position=[math.radians(120)]))
    assert node.neck_deg == pytest.approx(120.0)


def test_neck_angle_is_visible_in_the_web_camera(node):
    frames = []
    node.image_pub.publish = lambda msg: frames.append(bytes(msg.data))
    node._joint_states(JointState(name=['neck'], position=[0.0]))
    node._publish_camera()
    level = frames[-1]
    node._joint_states(JointState(name=['neck'], position=[math.radians(120)]))
    node._publish_camera()
    looking_up = frames[-1]
    # The horizon and projected walls shift when the head looks up.
    assert level != looking_up


@pytest.mark.parametrize('start,heading', [((3.0,2.0),90), ((2.0,.6),90)])
def test_long_frame_cannot_tunnel_through_wall_or_furniture(node, start, heading):
    node.x, node.y = start
    node.theta = heading
    node._applied_twist(twist(linear=3.0))
    node.last_tick_at = time.monotonic()-2.0
    node._tick()
    limit = 4.0 if start[1] == 2.0 else 2.4
    assert node.x <= limit-node.body_radius


def test_open_door_and_reverse_motion(node):
    node.x, node.y, node.theta = .5, 2.0, 90
    node._applied_twist(twist(linear=-.5))
    node.last_tick_at = time.monotonic()-2.0
    node._tick()
    assert node.x < 0 and node.y == pytest.approx(2.0)


def test_motor_telemetry_stops_with_watchdog(node):
    import json
    states = []
    node.world_state_pub.publish = lambda msg: states.append(json.loads(msg.data))
    node._applied_twist(twist(linear=.2, angular=.4))
    node._tick()
    assert states[-1]['applied_twist'] == {'linear_mps': .2, 'angular_rps': .4}
    assert states[-1]['wheel_separation_m'] == .2
    assert states[-1]['robot_dimensions_m'] == {
        'width': .32, 'height': .32, 'collision_radius': .16}
    assert states[-1]['lidar_pose']['x'] == pytest.approx(node.x + .03)
    assert states[-1]['camera_pose']['x'] == pytest.approx(node.x - .03)
    node.last_twist_at -= 10
    node._tick()
    assert states[-1]['applied_twist'] == {'linear_mps': 0, 'angular_rps': 0}
