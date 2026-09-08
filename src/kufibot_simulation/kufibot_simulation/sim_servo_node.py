"""Runs the real ServoNode with a no-op PWM driver, for simulation.

joint_states already reflects commanded-position estimates, not encoder
feedback, so no fake hardware behaviour needs to be modeled here.
"""
import rclpy
from kufibot_actuators.servo_node import ServoNode


class NullServoDriver:
    def __init__(self, address, busnum=1, debug=False):
        pass

    def set_pwm_freq(self, freq_hz):
        pass

    def set_pulse_us(self, channel, pulse_us, freq_hz=50):
        pass

    def close(self):
        pass


def main(args=None):
    rclpy.init(args=args)
    node = ServoNode(driver_factory=NullServoDriver)
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
