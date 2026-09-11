"""SimDcMotorNode reuses DcMotorNode's real clamping/watchdog logic verbatim;
only the PCA9685 tail is faked, so we assert the round-trip is lossless.
"""
import time

import pytest
import rclpy
from rclpy.clock import Clock
from kufibot_interfaces.msg import DriveCommand

from kufibot_simulation.sim_dc_motor_node import SimDcMotorNode


@pytest.fixture
def node():
    rclpy.init(args=['--ros-args', '-p', 'navigation_calibrated:=true',
                      '-p', 'navigation_max_linear:=0.2', '-p', 'navigation_max_angular:=0.4'])
    n = SimDcMotorNode()
    n.captured = []
    n.twist_pub.publish = lambda msg: n.captured.append((msg.linear.x, msg.angular.z))
    yield n
    n.destroy_node()
    rclpy.shutdown()


def command(profile, linear=0.0, angular=0.0):
    msg = DriveCommand(profile=profile)
    msg.header.stamp = Clock().now().to_msg()
    msg.twist.linear.x, msg.twist.angular.z = linear, angular
    return msg


def test_navigation_forward_reports_the_same_linear_speed(node):
    node.drive_callback(command('navigation', linear=0.1))
    assert len(node.captured) == 1  # never publish the intermediate one-wheel turn
    assert node.captured[-1] == pytest.approx((0.1, 0.0), abs=1e-6)


def test_navigation_turn_reports_the_same_angular_speed(node):
    node.drive_callback(command('navigation', angular=0.3))
    assert node.captured[-1] == pytest.approx((0.0, 0.3), abs=1e-6)


def test_manual_profile_still_applies_its_own_minimum_speed(node):
    node.drive_callback(command('manual', linear=0.01))
    linear, angular = node.captured[-1]
    assert linear == pytest.approx(node.min_linear, abs=1e-6)
    assert angular == pytest.approx(0.0, abs=1e-6)


def test_navigation_demand_is_never_boosted_to_the_manual_minimum(node):
    node.drive_callback(command('navigation', linear=0.02))
    linear, _ = node.captured[-1]
    assert linear == pytest.approx(0.02, abs=1e-6)


def test_invalid_navigation_demand_stops(node):
    node.drive_callback(command('navigation', linear=0.5))  # exceeds navigation_max_linear
    assert node.captured[-1] == pytest.approx((0.0, 0.0), abs=1e-6)


def test_watchdog_timeout_reports_stop(node):
    node.drive_callback(command('navigation', linear=0.1))
    node.last_cmd_time = time.monotonic() - 1.0
    node.captured.clear()
    node.safety_check()
    assert len(node.captured) == 1
    assert node.captured[-1] == pytest.approx((0.0, 0.0), abs=1e-6)


def test_obstacle_stop_and_resume_never_emit_a_spurious_turn(node):
    node.drive_callback(command('navigation', linear=.1))
    node.drive_callback(command('stop'))
    node.drive_callback(command('navigation', linear=.1))
    assert node.captured == pytest.approx([(.1, 0.), (0., 0.), (.1, 0.)])
