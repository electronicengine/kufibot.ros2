#!/usr/bin/env python3
"""Run a sequential, conservative motion test for every Kufibot servo."""

import json
from pathlib import Path

import kufibot_actuators
import rclpy
from kufibot_actuators.servo_node import DEFAULT_ANGLES, JOINT_CHANNELS
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32


TEST_POSES = ('halfup', 'halfdown', 'middle')
EYE_TEST_POSES = ('fullup', 'fulldown')
EYE_JOINTS = {'eyeLeft', 'eyeRight'}


class ServoAxisTest(Node):
    """Publish safe forward/backward targets one joint at a time."""

    def __init__(self):
        super().__init__('servo_axis_test')

        self.declare_parameter('hold_seconds', 2.0)
        self.declare_parameter('discovery_timeout_seconds', 30.0)
        self.declare_parameter('joint_config_file', '')

        self.hold_seconds = float(self.get_parameter('hold_seconds').value)
        self.discovery_timeout = float(
            self.get_parameter('discovery_timeout_seconds').value)
        if self.hold_seconds <= 0.0 or self.discovery_timeout <= 0.0:
            raise ValueError('Test timing parameters must be positive')

        configured_path = str(self.get_parameter('joint_config_file').value)
        config_path = (Path(configured_path).expanduser() if configured_path
                       else Path(kufibot_actuators.__file__).with_name(
                           'joint_angles.json'))
        self.test_sequence = self._load_sequence(config_path)
        self._joint_publishers = {
            joint: self.create_publisher(
                Float32, f'servo/{joint}/angle_deg', 10)
            for joint in JOINT_CHANNELS
        }

        self.sequence_index = 0
        self.discovery_elapsed = 0.0
        self.finished = False
        self.startup_complete = False
        self.state_subscription = self.create_subscription(
            JointState, 'servo/joint_states', self._on_joint_states, 10)
        self.timer_period = 0.1
        self.timer = self.create_timer(self.timer_period, self._tick)
        self.get_logger().info(
            'Waiting for servo_node startup pose before axis test...')

    @staticmethod
    def _load_sequence(path):
        try:
            with path.open(encoding='utf-8') as config_file:
                config = json.load(config_file)
            sequence = []
            for joint in JOINT_CHANNELS:
                poses = (EYE_TEST_POSES
                         if joint in EYE_JOINTS else TEST_POSES)
                sequence.extend(
                    (joint, pose, float(config[joint][pose]['angle']))
                    for pose in poses
                )
                sequence.append((joint, 'idle', DEFAULT_ANGLES[joint]))
            return sequence
        except (OSError, json.JSONDecodeError, KeyError,
                TypeError, ValueError) as error:
            raise RuntimeError(
                f'Cannot create axis test from {path}: {error}') from error

    def _on_joint_states(self, msg):
        self.startup_complete = set(JOINT_CHANNELS).issubset(msg.name)

    def _servo_node_is_ready(self):
        return self.startup_complete and all(
            publisher.get_subscription_count() > 0
            for publisher in self._joint_publishers.values())

    def _tick(self):
        if self.finished:
            return

        if not self._servo_node_is_ready():
            self.discovery_elapsed += self.timer_period
            if self.discovery_elapsed >= self.discovery_timeout:
                self.get_logger().error(
                    'servo_node startup not ready; no motion commands were sent')
                self.finished = True
            return

        self.timer.cancel()
        self.get_logger().info(
            'servo_node found; starting sequential axis test')
        self._publish_next()

    def _publish_next(self):
        if self.sequence_index >= len(self.test_sequence):
            self.get_logger().info('Axis test complete')
            self.finished = True
            return

        joint, pose, angle = self.test_sequence[self.sequence_index]
        message = Float32()
        message.data = angle
        self._joint_publishers[joint].publish(message)
        self.get_logger().info(
            f'Testing {joint}: {pose} ({angle:g} degrees)')
        self.sequence_index += 1
        self.timer = self.create_timer(self.hold_seconds, self._hold_done)

    def _hold_done(self):
        self.timer.cancel()
        self._publish_next()


def main(args=None):
    rclpy.init(args=args)
    node = ServoAxisTest()
    try:
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        node.get_logger().warning('Axis test interrupted by operator')
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
