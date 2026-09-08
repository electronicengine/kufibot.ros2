#!/usr/bin/env python3
"""Control Kufibot's differential-drive DC motors."""

import math
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from kufibot_interfaces.msg import DriveCommand
from .dc_motor_config import DEFAULT_CONFIG_PATH, load_speed_limits
from .pca9685 import PCA9685


class DcMotorNode(Node):
    # Motor-Driver-HAT kanal haritası (TB6612FNG)
    PWM_A = 0
    AIN1 = 1
    AIN2 = 2
    PWM_B = 5
    BIN1 = 3
    BIN2 = 4

    def __init__(self):
        super().__init__('dc_motor_node')

        self.declare_parameter('i2c_address', 0x50)
        self.declare_parameter('i2c_busnum', 1)
        self.declare_parameter('pwm_freq_hz', 50)
        self.declare_parameter('wheel_separation_m', 0.2)
        self.declare_parameter('motor_config_file', '')
        # Both motors were running opposite to the requested goal direction.
        # Flip the complete drivetrain polarity while keeping the left/right
        # differential-drive relationship unchanged.
        self.declare_parameter('invert_left', False)
        self.declare_parameter('invert_right', True)
        self.declare_parameter('cmd_timeout_sec', 0.5)
        self.declare_parameter('navigation_calibrated', False)
        self.declare_parameter('navigation_max_linear', 0.08)
        self.declare_parameter('navigation_max_angular', 0.20)

        address = self.get_parameter('i2c_address').value
        busnum = self.get_parameter('i2c_busnum').value
        freq = self.get_parameter('pwm_freq_hz').value
        self.wheel_sep = self.get_parameter('wheel_separation_m').value
        self.invert_left = self.get_parameter('invert_left').value
        self.invert_right = self.get_parameter('invert_right').value
        self.cmd_timeout = self.get_parameter('cmd_timeout_sec').value

        configured_path = str(self.get_parameter('motor_config_file').value)
        config_path = (Path(configured_path).expanduser() if configured_path
                       else DEFAULT_CONFIG_PATH)
        (self.min_linear, self.max_linear,
         self.min_angular, self.max_angular) = load_speed_limits(config_path)

        self.driver = PCA9685(address, busnum=busnum)
        self.driver.set_pwm_freq(freq)

        self.last_cmd_time = time.monotonic()
        self.sub = self.create_subscription(
            DriveCommand, 'drive/command', self.drive_callback, 1)
        self.safety_timer = self.create_timer(0.1, self.safety_check)

        self.get_logger().info(
            f'DC motor node başlatıldı (adres=0x{address:02X}).')

    def _set_motor(self, pwm_ch, in1_ch, in2_ch, speed_percent, inverted):
        speed_percent = max(-100.0, min(100.0, speed_percent))
        if inverted:
            speed_percent = -speed_percent

        if speed_percent >= 0:
            self.driver.set_level(in1_ch, 0)
            self.driver.set_level(in2_ch, 1)
        else:
            self.driver.set_level(in1_ch, 1)
            self.driver.set_level(in2_ch, 0)

        self.driver.set_duty_cycle(pwm_ch, abs(speed_percent))

    @staticmethod
    def _clamp_speed(speed, minimum, maximum):
        """Preserve stop commands and clamp motion to configured limits."""
        if speed == 0.0:
            return 0.0
        return math.copysign(min(maximum, max(minimum, abs(speed))), speed)

    def drive_callback(self, msg):
        # Profile and command arrive atomically. A navigation demand can never
        # accidentally be raised to the manual profile's minimum speed.
        age = (self.get_clock().now().nanoseconds / 1e9 -
               (msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9))
        if not 0 <= age <= self.cmd_timeout:
            self._stop_motors()
            return
        if msg.profile == 'manual':
            self.cmd_callback(msg.twist)
        elif msg.profile == 'navigation' and self.get_parameter('navigation_calibrated').value:
            linear, angular = msg.twist.linear.x, msg.twist.angular.z
            max_linear = float(self.get_parameter('navigation_max_linear').value)
            max_angular = float(self.get_parameter('navigation_max_angular').value)
            if (not all(math.isfinite(v) for v in (linear, angular, max_linear, max_angular))
                    or max_linear <= 0 or max_angular <= 0
                    or not 0 <= linear <= max_linear or abs(angular) > max_angular
                    or (linear != 0 and angular != 0)):
                self._stop_motors()
                return
            self.last_cmd_time = time.monotonic()
            self._apply_speed(linear, angular)
        else:
            self._stop_motors()

    def _stop_motors(self):
        self.driver.set_duty_cycle(self.PWM_A, 0)
        self.driver.set_duty_cycle(self.PWM_B, 0)

    def cmd_callback(self, msg: Twist):
        if not all(math.isfinite(v) for v in (msg.linear.x, msg.angular.z)):
            self._stop_motors()
            return
        self.last_cmd_time = time.monotonic()

        linear = self._clamp_speed(
            msg.linear.x, self.min_linear, self.max_linear)
        angular = self._clamp_speed(
            msg.angular.z, self.min_angular, self.max_angular)

        self._apply_speed(linear, angular)

    def _apply_speed(self, linear, angular):
        v_left = linear - angular * self.wheel_sep / 2.0
        v_right = linear + angular * self.wheel_sep / 2.0

        left_percent = (v_left / self.max_linear) * 100.0
        right_percent = (v_right / self.max_linear) * 100.0

        self._set_motor(
            self.PWM_A, self.AIN1, self.AIN2,
            left_percent, self.invert_left)
        self._set_motor(
            self.PWM_B, self.BIN1, self.BIN2,
            right_percent, self.invert_right)

    def safety_check(self):
        elapsed = time.monotonic() - self.last_cmd_time
        if elapsed > self.cmd_timeout:
            self.driver.set_duty_cycle(self.PWM_A, 0)
            self.driver.set_duty_cycle(self.PWM_B, 0)

    def destroy_node(self):
        self.driver.set_duty_cycle(self.PWM_A, 0)
        self.driver.set_duty_cycle(self.PWM_B, 0)
        self.driver.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = DcMotorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
