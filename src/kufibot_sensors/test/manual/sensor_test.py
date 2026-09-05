#!/usr/bin/env python3
"""Read every Kufibot sensor topic and validate one sample from each."""

import math

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, MagneticField, Range
from std_msgs.msg import Float32


class SensorTest(Node):
    """Wait for finite, physically plausible samples from all sensor nodes."""

    def __init__(self):
        super().__init__('sensor_test')

        self.declare_parameter('timeout_seconds', 10.0)
        self.declare_parameter('min_battery_voltage', 1.0)
        self.declare_parameter('max_battery_voltage', 16.0)

        self.timeout_seconds = float(
            self.get_parameter('timeout_seconds').value)
        self.min_battery_voltage = float(
            self.get_parameter('min_battery_voltage').value)
        self.max_battery_voltage = float(
            self.get_parameter('max_battery_voltage').value)
        if self.timeout_seconds <= 0.0:
            raise ValueError('timeout_seconds must be positive')
        if not 0.0 <= self.min_battery_voltage < self.max_battery_voltage:
            raise ValueError('Battery voltage limits are invalid')

        self.valid = {
            'battery_state': False,
            'compass/mag': False,
            'compass/heading_deg': False,
            'lidar/range': False,
        }
        self.last_errors = {}
        self.finished = False
        self.succeeded = False
        self.elapsed = 0.0
        self.timer_period = 0.1

        self._sensor_subscriptions = [
            self.create_subscription(
                BatteryState, 'battery_state', self._battery_callback, 10),
            self.create_subscription(
                MagneticField, 'compass/mag', self._mag_callback, 10),
            self.create_subscription(
                Float32, 'compass/heading_deg', self._heading_callback, 10),
            self.create_subscription(
                Range, 'lidar/range', self._range_callback, 10),
        ]
        self.timer = self.create_timer(self.timer_period, self._tick)
        self.get_logger().info(
            'Waiting for INA219, HMC5883L and TF-Luna sensor data...')

    def _accept(self, topic, summary):
        if self.valid[topic]:
            return
        self.valid[topic] = True
        self.last_errors.pop(topic, None)
        self.get_logger().info(f'PASS {topic}: {summary}')

    def _reject(self, topic, reason):
        self.last_errors[topic] = reason

    def _battery_callback(self, msg):
        if not math.isfinite(msg.voltage):
            self._reject('battery_state', 'voltage is not finite')
            return
        if not (
            self.min_battery_voltage
            <= msg.voltage
            <= self.max_battery_voltage
        ):
            self._reject(
                'battery_state', f'voltage {msg.voltage:g} V is outside '
                f'{self.min_battery_voltage:g}..'
                f'{self.max_battery_voltage:g} V')
            return
        if not math.isfinite(msg.current):
            self._reject('battery_state', 'current is not finite')
            return
        if not msg.present:
            self._reject('battery_state', 'battery is marked absent')
            return
        self._accept(
            'battery_state',
            f'{msg.voltage:g} V, {msg.current:g} A')

    def _mag_callback(self, msg):
        values = (
            msg.magnetic_field.x,
            msg.magnetic_field.y,
            msg.magnetic_field.z,
        )
        if not all(math.isfinite(value) for value in values):
            self._reject('compass/mag', 'field contains a non-finite value')
            return
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude <= 0.0:
            self._reject('compass/mag', 'field magnitude is zero')
            return
        self._accept(
            'compass/mag',
            f'x={values[0]:g}, y={values[1]:g}, z={values[2]:g} T')

    def _heading_callback(self, msg):
        if not math.isfinite(msg.data) or not 0.0 <= msg.data < 360.0:
            self._reject(
                'compass/heading_deg',
                f'heading {msg.data:g} is outside 0..<360 degrees')
            return
        self._accept('compass/heading_deg', f'{msg.data:g} degrees')

    def _range_callback(self, msg):
        metadata = (msg.field_of_view, msg.min_range, msg.max_range)
        if not all(math.isfinite(value) for value in metadata):
            self._reject('lidar/range', 'range metadata is not finite')
            return
        if (msg.field_of_view <= 0.0
                or not 0.0 <= msg.min_range < msg.max_range):
            self._reject('lidar/range', 'range metadata is invalid')
            return
        if not math.isfinite(msg.range):
            self._reject('lidar/range', 'distance is not finite')
            return
        if not msg.min_range <= msg.range <= msg.max_range:
            self._reject(
                'lidar/range', f'distance {msg.range:g} m is outside '
                f'{msg.min_range:g}..{msg.max_range:g} m')
            return
        self._accept('lidar/range', f'{msg.range:g} m')

    def _tick(self):
        if self.finished:
            return
        if all(self.valid.values()):
            self.get_logger().info('All sensor data is valid')
            self.succeeded = True
            self.finished = True
            return

        self.elapsed += self.timer_period
        if self.elapsed < self.timeout_seconds:
            return

        for topic, is_valid in self.valid.items():
            if is_valid:
                continue
            reason = self.last_errors.get(topic, 'no data received')
            self.get_logger().error(f'FAIL {topic}: {reason}')
        self.get_logger().error('Sensor test failed')
        self.finished = True


def main(args=None):
    """Run the sensor test and return a process exit status."""
    rclpy.init(args=args)
    node = SensorTest()
    try:
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        node.get_logger().warning('Sensor test interrupted')
    finally:
        succeeded = node.succeeded
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0 if succeeded else 1


if __name__ == '__main__':
    raise SystemExit(main())
