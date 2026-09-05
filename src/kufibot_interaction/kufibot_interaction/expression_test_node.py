#!/usr/bin/env python3
"""Play every configured Kufibot expression in order for hardware checks."""

import time

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter

from kufibot_interfaces.msg import JointCommand

from .expression_engine import ExpressionLibrary


class ExpressionTestNode(Node):
    """Publish the expression catalogue to the normal agent command topic."""

    def __init__(self):
        super().__init__('expression_test_node')
        self.declare_parameter(
            'gesture_config_file',
            '/home/kufi/workspace/kufibot.cpp/config/gesture_config.json')
        self.declare_parameter(
            'motion_config_file',
            '/home/kufi/workspace/kufibot.cpp/config/motion_definitions.json')
        self.declare_parameter(
            'joint_angles_file',
            '/home/kufi/workspace/kufibot.cpp/config/joint_angles.json')
        self.declare_parameter('pause_sec', 1.0)
        self.declare_parameter('repeat', False)
        self.declare_parameter('motions', Parameter.Type.STRING_ARRAY)

        self.library = ExpressionLibrary(
            self.get_parameter('gesture_config_file').value,
            self.get_parameter('motion_config_file').value,
            self.get_parameter('joint_angles_file').value)
        self.pause_sec = max(0.0, float(self.get_parameter('pause_sec').value))
        requested = list(self.get_parameter('motions').value or [])
        self.names = self._select_motions(requested)
        self.repeat = bool(self.get_parameter('repeat').value)
        self.publisher = self.create_publisher(
            JointCommand, 'servo/agent_targets', 10)
        self.motion_index = 0
        self.event_index = 0
        self.motion_started_at = None
        self.pause_until = None
        self.finished = False
        self.create_timer(0.02, self._tick)

        self.get_logger().info(
            'Expression test ready: ' + ', '.join(self.names)
            + (' (repeating)' if self.repeat else ''))

    def _select_motions(self, requested):
        if not requested:
            return list(self.library.motions)
        missing = [
            name for name in requested if name not in self.library.motions]
        if missing:
            raise ValueError('Unknown expression(s): ' + ', '.join(missing))
        return requested

    def _publish(self, targets, hold_sec):
        msg = JointCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.names = list(targets)
        msg.angles_deg = list(targets.values())
        msg.hold_sec = max(0.01, float(hold_sec))
        self.publisher.publish(msg)

    def _return_to_idle(self):
        self._publish(self.library.idle, 0.5)

    def _tick(self):
        if self.finished:
            return
        now = time.monotonic()
        if self.pause_until is not None:
            if now < self.pause_until:
                return
            self.pause_until = None

        motion = self.library.motions[self.names[self.motion_index]]
        if self.motion_started_at is None:
            self.motion_started_at = now
            self.event_index = 0
            self.pose = dict(self.library.idle)
            self.get_logger().info('Playing expression: ' + motion['name'])

        elapsed_ms = int((now - self.motion_started_at) * 1000.0)
        while (self.event_index < len(motion['events'])
               and elapsed_ms >= motion['events'][self.event_index][0]):
            _, changes = motion['events'][self.event_index]
            self.pose.update(changes)
            remaining = max(0.5, (motion['duration_ms'] - elapsed_ms) / 1000.0)
            self._publish(self.pose, remaining)
            self.event_index += 1

        if elapsed_ms < motion['duration_ms']:
            return
        self._return_to_idle()
        self.motion_started_at = None
        self.motion_index += 1
        if self.motion_index == len(self.names):
            if self.repeat:
                self.motion_index = 0
            else:
                self.finished = True
                self.get_logger().info('All expression tests completed.')
                return
        self.pause_until = now + self.pause_sec

    def stop(self):
        """Leave the robot in its configured idle pose before shutdown."""
        self._return_to_idle()


def main(args=None):
    rclpy.init(args=args)
    node = ExpressionTestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
