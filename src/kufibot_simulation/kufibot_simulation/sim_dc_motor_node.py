"""Runs the real DcMotorNode with a PWM shim instead of I2C hardware.

Reuses DcMotorNode's clamping, profile arbitration and watchdog logic
unmodified; only the PCA9685 tail is replaced so simulated wheel motion can be
recovered from the same duty-cycle/level calls the real driver would receive.
"""
import rclpy
from geometry_msgs.msg import Twist
from kufibot_actuators import dc_motor_node as dc_motor_module
from kufibot_actuators.dc_motor_node import DcMotorNode


class FakePCA9685:
    """Decodes set_level/set_duty_cycle calls back into per-channel state."""

    def __init__(self, address, busnum=1, debug=False):
        self.levels = {}
        self.duties = {}
        self.on_change = None

    def set_pwm_freq(self, freq_hz):
        pass

    def set_level(self, channel, value):
        self.levels[channel] = bool(value)
        self._emit()

    def set_duty_cycle(self, channel, percent):
        self.duties[channel] = percent
        self._emit()

    def close(self):
        pass

    def _emit(self):
        if self.on_change is not None:
            self.on_change()


class SimDcMotorNode(DcMotorNode):
    def __init__(self):
        original = dc_motor_module.PCA9685
        dc_motor_module.PCA9685 = FakePCA9685
        try:
            super().__init__()
        finally:
            dc_motor_module.PCA9685 = original
        self.twist_pub = self.create_publisher(Twist, 'simulation/applied_twist', 1)
        self.driver.on_change = self._publish_applied_twist

    def _wheel_speed_mps(self, pwm_channel, in2_channel, inverted):
        duty = self.driver.duties.get(pwm_channel, 0.0)
        sign = 1.0 if self.driver.levels.get(in2_channel, False) else -1.0
        speed = sign * duty / 100.0 * self.max_linear
        return -speed if inverted else speed

    def _publish_applied_twist(self):
        v_left = self._wheel_speed_mps(self.PWM_A, self.AIN2, self.invert_left)
        v_right = self._wheel_speed_mps(self.PWM_B, self.BIN2, self.invert_right)
        twist = Twist()
        twist.linear.x = (v_left + v_right) / 2.0
        twist.angular.z = (v_right - v_left) / self.wheel_sep
        self.twist_pub.publish(twist)


def main(args=None):
    rclpy.init(args=args)
    node = SimDcMotorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
