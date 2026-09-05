#!/usr/bin/env python3
"""Arbitrate agent and visual-tracking commands before servo hardware."""

import math
import threading

import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import JointState

from kufibot_interfaces.msg import JointCommand
from .joint_limits import JOINT_LIMITS


class ServoArbiter(Node):
    """Give valid, temporary agent commands priority over visual tracking."""

    TRACKING_JOINTS = {'neck', 'headLeftRight'}

    def __init__(self):
        super().__init__('servo_arbiter')
        self.declare_parameter('default_agent_hold_sec', 2.0)
        self.declare_parameter('max_agent_hold_sec', 10.0)
        self.declare_parameter('publish_rate_hz', 20.0)
        self.default_hold = float(self.get_parameter(
            'default_agent_hold_sec').value)
        self.max_hold = float(self.get_parameter('max_agent_hold_sec').value)
        rate = float(self.get_parameter('publish_rate_hz').value)
        self._lock = threading.Lock()
        self._agent = {}
        self._tracking = {}
        self._agent_until = None
        self.publisher = self.create_publisher(
            JointState, 'servo/joint_targets', 10)
        self.create_subscription(
            JointCommand, 'servo/agent_targets', self._agent_callback, 10)
        self.create_subscription(
            JointCommand, 'servo/tracking_targets', self._tracking_callback, 10)
        self.create_subscription(
            JointState, 'servo/joint_states', self._state_callback, 10)
        self._current = {}
        self.create_timer(1.0 / rate, self._publish)

    def _validated(self, msg):
        if len(msg.names) != len(msg.angles_deg):
            self.get_logger().warning('Rejected command with mismatched arrays')
            return None
        result = {}
        for name, angle in zip(msg.names, msg.angles_deg):
            if name not in JOINT_LIMITS or not math.isfinite(angle):
                self.get_logger().warning(f'Rejected invalid joint target: {name}')
                return None
            low, high = JOINT_LIMITS[name]
            result[name] = max(low, min(high, float(angle)))
        return result

    def _agent_callback(self, msg):
        if msg.cancel_agent:
            with self._lock:
                self._agent.clear()
                self._agent_until = None
            return
        targets = self._validated(msg)
        if targets is None:
            return
        hold = msg.hold_sec if msg.hold_sec > 0.0 else self.default_hold
        hold = min(float(hold), self.max_hold)
        with self._lock:
            self._agent.update(targets)
            self._agent_until = self.get_clock().now() + Duration(seconds=hold)

    def _tracking_callback(self, msg):
        targets = self._validated(msg)
        if targets is None or not set(targets).issubset(self.TRACKING_JOINTS):
            self.get_logger().warning('Tracking may only control head/eye joints')
            return
        with self._lock:
            self._tracking = targets

    def _state_callback(self, msg):
        if len(msg.name) == len(msg.position):
            self._current = dict(zip(msg.name, msg.position))

    def _publish(self):
        now = self.get_clock().now()
        with self._lock:
            agent_active = self._agent_until is not None and now < self._agent_until
            targets = dict(self._tracking)
            if agent_active:
                targets.update(self._agent)
            else:
                # Non-tracking joints retain their last agent target.
                targets.update({
                    name: angle for name, angle in self._agent.items()
                    if name not in self.TRACKING_JOINTS
                })
        if not targets:
            return
        msg = JointState()
        msg.header.stamp = now.to_msg()
        msg.name = list(targets)
        msg.position = [math.radians(targets[name]) for name in msg.name]
        self.publisher.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = ServoArbiter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
