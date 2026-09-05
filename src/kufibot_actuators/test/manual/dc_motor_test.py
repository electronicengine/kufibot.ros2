#!/usr/bin/env python3
"""Run a full-speed, sequential motion test for the Kufibot DC motors."""

from pathlib import Path

import kufibot_actuators
import rclpy
from geometry_msgs.msg import Twist
from kufibot_actuators.dc_motor_config import load_speed_limits
from rclpy.node import Node


class DcMotorTest(Node):
    """Exercise forward, backward and turning motion with stop intervals."""

    def __init__(self):
        super().__init__('dc_motor_test')

        self.declare_parameter('motor_config_file', '')
        self.declare_parameter('hold_seconds', 2.0)
        self.declare_parameter('pause_seconds', 1.0)
        self.declare_parameter('discovery_timeout_seconds', 5.0)

        configured_path = str(self.get_parameter('motor_config_file').value)
        config_path = (Path(configured_path).expanduser() if configured_path
                       else Path(kufibot_actuators.__file__).with_name(
                           'dc_motor_config.json'))
        _, linear, _, angular = load_speed_limits(config_path)
        self.hold_seconds = float(self.get_parameter('hold_seconds').value)
        self.pause_seconds = float(self.get_parameter('pause_seconds').value)
        self.discovery_timeout = float(
            self.get_parameter('discovery_timeout_seconds').value)
        if (self.hold_seconds <= 0.0 or self.pause_seconds <= 0.0
                or self.discovery_timeout <= 0.0):
            raise ValueError('Test timing parameters must be positive')

        self.commands = (
            ('forward', linear, 0.0),
            ('backward', -linear, 0.0),
            ('turn left', 0.0, angular),
            ('turn right', 0.0, -angular),
        )
        self.publisher = self.create_publisher(Twist, 'cmd_vel', 10)
        self.timer_period = 0.1
        self.timer = self.create_timer(self.timer_period, self._tick)
        self.discovery_elapsed = 0.0
        self.phase_elapsed = 0.0
        self.command_index = 0
        self.is_paused = True
        self.started = False
        self.finished = False
        self.get_logger().info(
            'Waiting for dc_motor_node subscription before motor test...')

    def _publish(self, linear, angular):
        message = Twist()
        message.linear.x = linear
        message.angular.z = angular
        self.publisher.publish(message)

    def stop(self):
        """Publish an explicit zero-velocity safety command."""
        self._publish(0.0, 0.0)

    def _tick(self):
        if self.finished:
            self.stop()
            return

        if not self.started:
            if self.publisher.get_subscription_count() == 0:
                self.discovery_elapsed += self.timer_period
                if self.discovery_elapsed >= self.discovery_timeout:
                    self.get_logger().error(
                        'dc_motor_node was not found; no motion was commanded')
                    self.finished = True
                return
            self.started = True
            self.get_logger().info(
                'dc_motor_node found; starting full-speed motor test')

        self.phase_elapsed += self.timer_period
        if self.is_paused:
            self.stop()
            if self.phase_elapsed >= self.pause_seconds:
                if self.command_index >= len(self.commands):
                    self.get_logger().info('DC motor test complete; stopped')
                    self.finished = True
                    return
                label, linear, angular = self.commands[self.command_index]
                self.get_logger().info(
                    f'Testing {label}: linear={linear:g} m/s, '
                    f'angular={angular:g} rad/s')
                self.is_paused = False
                self.phase_elapsed = 0.0
            return

        _, linear, angular = self.commands[self.command_index]
        self._publish(linear, angular)
        if self.phase_elapsed >= self.hold_seconds:
            self.stop()
            self.command_index += 1
            self.is_paused = True
            self.phase_elapsed = 0.0


def main(args=None):
    rclpy.init(args=args)
    node = DcMotorTest()
    try:
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        node.get_logger().warning('DC motor test interrupted; stopping')
    finally:
        node.stop()
        rclpy.spin_once(node, timeout_sec=0.1)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
