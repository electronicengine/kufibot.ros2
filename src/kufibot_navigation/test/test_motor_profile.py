from types import SimpleNamespace
import time
import pytest
from rclpy.time import Time
from geometry_msgs.msg import Twist
from kufibot_interfaces.msg import DriveCommand
from kufibot_actuators.dc_motor_node import DcMotorNode


def motor():
    n = DcMotorNode.__new__(DcMotorNode)
    n.cmd_timeout = .5
    n.min_linear, n.max_linear = .25, .5
    n.min_angular, n.max_angular = 2.5, 5.
    n.last_cmd_time = time.monotonic()
    n.get_clock = lambda: SimpleNamespace(now=lambda: Time(seconds=10))
    n.get_parameter = lambda key: SimpleNamespace(value={
        'navigation_calibrated': True, 'navigation_max_linear': .08,
        'navigation_max_angular': .2}[key])
    applied = []
    n._apply_speed = lambda linear, angular: applied.append((linear, angular))
    n._stop_motors = lambda: applied.append((0., 0.))
    return n, applied


def command(profile, linear=.05, angular=0.):
    msg = DriveCommand(profile=profile)
    msg.header.stamp.sec = 10
    msg.twist.linear.x, msg.twist.angular.z = linear, angular
    return msg


def test_navigation_never_uses_manual_minimum_and_profiles_are_atomic():
    n, applied = motor()
    n.drive_callback(command('manual'))
    assert applied[-1] == (.25, 0.)
    n.drive_callback(command('navigation'))
    assert applied[-1] == (.05, 0.)
    n.drive_callback(command('stop'))
    assert applied[-1] == (0., 0.)


@pytest.mark.parametrize('msg', [command('navigation', -.05), command('navigation', .09),
    command('navigation', .05, .1), command('navigation', float('nan')), command('unknown')])
def test_invalid_profile_demand_stops(msg):
    n, applied = motor()
    n.drive_callback(msg)
    assert applied[-1] == (0., 0.)


def test_stale_or_uncalibrated_profile_stops():
    n, applied = motor()
    msg = command('navigation')
    msg.header.stamp.sec = 9
    n.drive_callback(msg)
    assert applied[-1] == (0., 0.)
    n.get_parameter = lambda key: SimpleNamespace(value=False)
    n.drive_callback(command('navigation'))
    assert applied[-1] == (0., 0.)


def test_watchdog_remains_independent_of_navigation_node():
    n, _ = motor()
    calls = []
    n.driver = SimpleNamespace(set_duty_cycle=lambda channel, duty: calls.append((channel, duty)))
    n.last_cmd_time = time.monotonic() - 1
    n.safety_check()
    assert calls == [(n.PWM_A, 0), (n.PWM_B, 0)]
