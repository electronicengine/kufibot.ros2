"""Verify startup sequencing without accessing physical hardware."""

import math
from unittest.mock import Mock

import pytest
import rclpy
from sensor_msgs.msg import JointState

from kufibot_actuators.servo_node import DEFAULT_ANGLES, JOINT_CHANNELS, ServoNode


@pytest.fixture
def servo(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(
        'kufibot_actuators.servo_node.time.monotonic', lambda: clock[0])
    rclpy.init()
    driver = Mock()
    node = ServoNode(driver_factory=lambda *args, **kwargs: driver)
    try:
        yield node, driver, clock
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_startup_writes_every_default_and_waits_before_commands(servo):
    node, driver, clock = servo
    node.joint_state_publisher = Mock()
    node._set_target('neck', 100.0)
    msg = JointState(name=['neck'], position=[math.radians(100.0)])
    node._joint_state_callback(msg)
    assert node.target_angles == DEFAULT_ANGLES
    node._publish_joint_states()
    node.joint_state_publisher.publish.assert_not_called()
    for index, (joint, channel) in enumerate(JOINT_CHANNELS.items()):
        node._motion_tick()
        assert driver.set_pulse_us.call_count == index + 1
        pulse = 500.0 + DEFAULT_ANGLES[joint] / 180.0 * 2000.0
        driver.set_pulse_us.assert_called_with(channel, pulse, 50.0)
        node._motion_tick()
        assert driver.set_pulse_us.call_count == index + 1
        assert not node.startup_complete
        clock[0] += node.startup_settle_sec
    node._motion_tick()
    assert node.startup_complete
    node._publish_joint_states()
    node.joint_state_publisher.publish.assert_called_once()
    node._set_target('neck', 100.0)
    node._motion_tick()
    assert node.current_angles['neck'] == 11.0


def test_failed_startup_write_blocks_and_retries_same_joint(servo):
    node, driver, clock = servo
    driver.set_pulse_us.side_effect = OSError('I2C unavailable')
    node._motion_tick()
    assert node._startup_index == 0
    node._set_target('neck', 100.0)
    node._motion_tick()
    assert driver.set_pulse_us.call_count == 1
    assert not node.startup_complete
    assert node.target_angles == DEFAULT_ANGLES
    clock[0] += node.startup_settle_sec
    driver.set_pulse_us.side_effect = None
    node._motion_tick()
    assert node._startup_index == 1
    assert driver.set_pulse_us.call_args_list[0] == driver.set_pulse_us.call_args_list[1]
    assert not node.startup_complete
