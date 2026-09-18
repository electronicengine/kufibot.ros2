#!/usr/bin/env python3
"""Safely control Kufibot's servo joints through a PCA9685 board."""

import json
import math
from pathlib import Path
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import Float32

from .pca9685 import PCA9685


# Channels follow ServoMotorJoint in the original C++ controller.
JOINT_CHANNELS = {
    'rightArm': 1, 'leftArm': 0, 'neck': 2, 'headLeftRight': 3,
    'eyeRight': 4, 'eyeLeft': 5,
}

# Startup pose from the original C++ controller.
DEFAULT_ANGLES = {
    'rightArm': 15.0, 'leftArm': 170.0, 'neck': 10.0,
    'headLeftRight': 90.0, 'eyeRight': 170.0, 'eyeLeft': 0.0,
}


class ServoNode(Node):
    """Apply per-joint limits and slew servo targets at a controlled rate."""

    def __init__(self, driver_factory=PCA9685):
        super().__init__('servo_node')

        self.declare_parameter('i2c_address', 0x40)
        self.declare_parameter('i2c_busnum', 1)
        self.declare_parameter('pwm_freq_hz', 50.0)
        self.declare_parameter('min_pulse_us', 500.0)
        self.declare_parameter('max_pulse_us', 2500.0)
        self.declare_parameter('step_deg', 1.0)
        self.declare_parameter('arm_step_deg', 0.3)
        self.declare_parameter('step_delay_ms', 15.0)
        self.declare_parameter('startup_settle_sec', 1.5)
        self.declare_parameter('joint_config_file', '')

        address = int(self.get_parameter('i2c_address').value)
        busnum = int(self.get_parameter('i2c_busnum').value)
        self.freq = float(self.get_parameter('pwm_freq_hz').value)
        self.min_pulse = float(self.get_parameter('min_pulse_us').value)
        self.max_pulse = float(self.get_parameter('max_pulse_us').value)
        self.step_deg = float(self.get_parameter('step_deg').value)
        self.arm_step_deg = float(self.get_parameter('arm_step_deg').value)
        delay = float(self.get_parameter('step_delay_ms').value) / 1000.0
        self.startup_settle_sec = float(
            self.get_parameter('startup_settle_sec').value)
        if (not math.isfinite(self.startup_settle_sec)
                or self.startup_settle_sec <= 0.0):
            raise ValueError('startup_settle_sec must be finite and positive')

        if self.freq <= 0.0 or self.min_pulse >= self.max_pulse:
            raise ValueError('PWM frequency and pulse limits are invalid')
        if self.step_deg <= 0.0 or self.arm_step_deg <= 0.0 or delay <= 0.0:
            raise ValueError(
                'step_deg, arm_step_deg and step_delay_ms must be positive')

        configured_path = str(self.get_parameter('joint_config_file').value)
        config_path = (Path(configured_path).expanduser() if configured_path
                       else Path(__file__).with_name('joint_angles.json'))
        self.positions, self.limits = self._load_joint_config(config_path)
        self.current_angles = {
            name: self._clamp(name, angle)
            for name, angle in DEFAULT_ANGLES.items()
        }
        self.target_angles = self.current_angles.copy()
        self._startup_joints = list(JOINT_CHANNELS)
        self._startup_index = 0
        self._startup_wait_until = 0.0
        self.startup_complete = False

        self.driver = driver_factory(address, busnum=busnum)
        self.driver.set_pwm_freq(self.freq)

        self._angle_subscriptions = []
        for joint in JOINT_CHANNELS:
            subscription = self.create_subscription(
                Float32, f'servo/{joint}/angle_deg',
                lambda msg, name=joint: self._set_target(name, msg.data), 10)
            self._angle_subscriptions.append(subscription)
        self.joint_state_subscription = self.create_subscription(
            JointState, 'servo/joint_targets', self._joint_state_callback, 10)
        self.joint_state_publisher = self.create_publisher(
            JointState, 'servo/joint_states', 10)
        self.motion_timer = self.create_timer(delay, self._motion_tick)
        self.state_timer = self.create_timer(0.1, self._publish_joint_states)

        ranges = ', '.join(
            f'{name}={lower:g}..{upper:g}'
            for name, (lower, upper) in self.limits.items())
        self.get_logger().info(
            f'Servo controller initializing at 0x{address:02X}; '
            f'limits: {ranges}; sequential startup pose in progress')

    def _load_joint_config(self, path):
        """Load named positions and derive each joint's mechanical limits."""
        try:
            with path.open(encoding='utf-8') as config_file:
                raw_config = json.load(config_file)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f'Cannot load joint configuration {path}: {error}') from error

        positions = {}
        limits = {}
        for joint in JOINT_CHANNELS:
            try:
                joint_positions = {
                    name: float(pose['angle'])
                    for name, pose in raw_config[joint].items()
                }
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f'Invalid or missing configuration for {joint}') from error
            angles = list(joint_positions.values())
            if not angles or not all(math.isfinite(angle) for angle in angles):
                raise ValueError(f'No valid positions configured for {joint}')
            if min(angles) < 0.0 or max(angles) > 180.0:
                raise ValueError(
                    f'{joint} angles must be within 0..180 degrees')
            positions[joint] = joint_positions
            limits[joint] = (min(angles), max(angles))
        return positions, limits

    def _clamp(self, joint, angle):
        lower, upper = self.limits[joint]
        return max(lower, min(upper, float(angle)))

    def _set_target(self, joint, requested_angle):
        # Discard startup commands so stale gestures cannot run after startup.
        if not self.startup_complete:
            return
        if not math.isfinite(requested_angle):
            self.get_logger().warning(
                f'Ignoring non-finite target for {joint}')
            return
        target = self._clamp(joint, requested_angle)
        if target != requested_angle:
            lower, upper = self.limits[joint]
            self.get_logger().warning(
                f'{joint} target {requested_angle:g} is outside '
                f'{lower:g}..{upper:g}; clamped to {target:g}')
        self.target_angles[joint] = target

    def _joint_state_callback(self, msg):
        if len(msg.name) != len(msg.position):
            self.get_logger().warning(
                'Ignoring JointState: name and position lengths differ')
            return
        for joint, angle_rad in zip(msg.name, msg.position):
            if joint not in JOINT_CHANNELS:
                self.get_logger().warning(f'Ignoring unknown joint: {joint}')
                continue
            # sensor_msgs/JointState positions are expressed in radians.
            self._set_target(joint, math.degrees(angle_rad))

    def _motion_tick(self):
        """Advance every active joint by at most one configured step."""
        if not self.startup_complete:
            self._startup_tick()
            return
        for joint, channel in JOINT_CHANNELS.items():
            current = self.current_angles[joint]
            difference = self.target_angles[joint] - current
            if abs(difference) < 1e-9:
                continue
            step = (self.arm_step_deg
                    if joint in ('leftArm', 'rightArm') else self.step_deg)
            movement = math.copysign(min(step, abs(difference)), difference)
            next_angle = current + movement
            pulse_us = self.min_pulse + (next_angle / 180.0) * (
                self.max_pulse - self.min_pulse)
            try:
                self.driver.set_pulse_us(channel, pulse_us, self.freq)
            except OSError as error:
                self.get_logger().error(
                    f'Failed to move {joint} on channel {channel}: {error}')
                continue
            self.current_angles[joint] = next_angle

    def _startup_tick(self):
        """Command one joint at a time, allowing it to settle before the next.

        PWM servos provide no position feedback. The initial pulse commands
        the default directly; slew limiting only applies after initialization.
        Completion means all writes and settling delays succeeded, not that
        physical arrival was measured.
        """
        if time.monotonic() < self._startup_wait_until:
            return
        if self._startup_index == len(self._startup_joints):
            self.startup_complete = True
            self.get_logger().info('Servo startup pose complete; commands enabled')
            return
        joint = self._startup_joints[self._startup_index]
        angle = self.current_angles[joint]
        pulse_us = self.min_pulse + (angle / 180.0) * (
            self.max_pulse - self.min_pulse)
        try:
            self.driver.set_pulse_us(JOINT_CHANNELS[joint], pulse_us, self.freq)
        except OSError as error:
            self.get_logger().error(
                f'Failed to initialize {joint}; startup blocked: {error}')
        else:
            self._startup_index += 1
        # Also back off on failed writes; never enable commands on failure.
        self._startup_wait_until = time.monotonic() + self.startup_settle_sec

    def _publish_joint_states(self):
        if not self.startup_complete:
            return
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = list(JOINT_CHANNELS)
        msg.position = [
            math.radians(self.current_angles[name]) for name in msg.name
        ]
        self.joint_state_publisher.publish(msg)

    def destroy_node(self):
        if hasattr(self, 'driver'):
            self.driver.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = ServoNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
