#!/usr/bin/env python3
"""Arbitrate agent and visual-tracking commands before servo hardware."""

import math
import json
import time
import threading

import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

from kufibot_interfaces.msg import JointCommand
from .joint_limits import JOINT_LIMITS


class ServoArbiter(Node):
    """Give valid, temporary agent commands priority over visual tracking."""

    TRACKING_JOINTS = {'neck', 'headLeftRight'}
    MOTION_JOINTS = {'leftArm', 'rightArm'}

    def __init__(self):
        super().__init__('servo_arbiter')
        self.declare_parameter('default_control_mode', 'remote')
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
        self._navigation = {}
        self._navigation_until = 0.0
        self._motion = {}
        self._motion_until = 0.0
        self._agent_until = None
        self.publisher = self.create_publisher(
            JointState, 'servo/joint_targets', 10)
        self.create_subscription(
            JointCommand, 'servo/agent_targets', self._agent_callback, 10)
        self.create_subscription(
            JointCommand, 'servo/navigation_targets', self._navigation_callback, 1)
        self.create_subscription(
            JointCommand, 'servo/motion_targets', self._motion_callback, 1)
        self.create_subscription(
            JointCommand, 'servo/tracking_targets', self._tracking_callback, 10)
        self.create_subscription(
            JointState, 'servo/joint_states', self._state_callback, 10)
        self._current = {}
        self._mode = str(self.get_parameter('default_control_mode').value)
        if self._mode not in ('ai', 'remote', 'tools'):
            raise ValueError('default_control_mode must be ai, remote or tools')
        self._remote = {}
        self._remote_seen = 0.0
        self.create_subscription(String, 'remote/command', self._remote_callback, 10)
        self.mode_pub = self.create_publisher(String, 'remote/applied_mode', 10)
        self.create_timer(1.0 / rate, self._publish)

    def _remote_callback(self, msg):
        try:
            data = json.loads(msg.data)
            mode, targets = data['mode'], data['targets']
            if mode not in ('ai', 'remote', 'tools') or not isinstance(targets, dict):
                return
            command = JointCommand()
            command.names = list(targets)
            command.angles_deg = [float(v) for v in targets.values()]
            valid = self._validated(command)
            if valid is None:
                return
        except (ValueError, TypeError, KeyError, AssertionError, OverflowError):
            return
        with self._lock:
            if mode != self._mode:
                self._navigation = {}
                self._navigation_until = 0.0
                self._agent.clear()
                self._tracking.clear()
                self._agent_until = None
                self._remote = {n: math.degrees(v) for n, v in self._current.items()
                                if n in JOINT_LIMITS and math.isfinite(v)}
            self._mode = mode
            if mode == 'remote':
                self._remote.update(valid)
            self._remote_seen = time.monotonic()

    def _navigation_callback(self, msg):
        with self._lock:
            if msg.cancel_agent or self._mode not in ('ai', 'tools'):
                self._navigation = {}
                self._navigation_until = 0.0
                return
            targets = self._validated(msg)
            if targets is None or set(targets) != self.TRACKING_JOINTS:
                return
            if not math.isfinite(msg.hold_sec) or msg.hold_sec <= 0:
                return
            self._navigation = targets
            self._navigation_until = time.monotonic() + min(.3, msg.hold_sec)

    def _motion_callback(self, msg):
        """Temporarily raise both arms whenever an applied drive is non-zero."""
        with self._lock:
            if msg.cancel_agent:
                self._motion = {}
                self._motion_until = 0.0
                return
            targets = self._validated(msg)
            if targets is None or set(targets) != self.MOTION_JOINTS:
                return
            if not math.isfinite(msg.hold_sec) or msg.hold_sec <= 0:
                return
            self._motion = targets
            self._motion_until = time.monotonic() + min(.3, msg.hold_sec)

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
        if self._mode == 'remote':
            return
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
        if self._mode == 'remote':
            return
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
            if (self._mode in ('ai', 'tools') and time.monotonic() < getattr(self, '_navigation_until', 0)):
                targets.update(self._navigation)
            if self._mode == 'remote':
                # A lost bridge holds the last position; it never enables AI.
                targets = dict(self._remote)
            # Motion posture has the highest arm priority in either control
            # mode, then immediately yields to the last user/agent pose.
            if time.monotonic() < self._motion_until:
                targets.update(self._motion)
        mode_msg = String()
        mode_msg.data = self._mode if time.monotonic() - self._remote_seen < 0.5 else 'unavailable'
        self.mode_pub.publish(mode_msg)
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
