#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from ina219 import INA219
from ina219 import DeviceRangeError


class Ina219Node(Node):
    def __init__(self):
        super().__init__('ina219_node')

        # Parametreler - launch dosyasından veya CLI'dan override edilebilir
        self.declare_parameter('shunt_ohms', 0.1)
        self.declare_parameter('max_expected_amps', 2.0)
        self.declare_parameter('i2c_address', 0x44)
        self.declare_parameter('i2c_busnum', 1)
        self.declare_parameter('publish_rate_hz', 5.0)

        shunt_ohms = self.get_parameter('shunt_ohms').value
        max_amps = self.get_parameter('max_expected_amps').value
        address = self.get_parameter('i2c_address').value
        busnum = self.get_parameter('i2c_busnum').value
        rate = self.get_parameter('publish_rate_hz').value

        try:
            self.ina = INA219(shunt_ohms, max_amps, address=address, busnum=busnum)
            self.ina.configure(self.ina.RANGE_16V)
            self.get_logger().info('INA219 sensörü başlatıldı.')
        except Exception as e:
            self.get_logger().error(f'INA219 başlatılamadı: {e}')
            raise

        self.publisher_ = self.create_publisher(BatteryState, 'battery_state', 10)
        self.timer = self.create_timer(1.0 / rate, self.timer_callback)

    def timer_callback(self):
        msg = BatteryState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'ina219'

        try:
            msg.voltage = float(self.ina.voltage())
            msg.current = float(self.ina.current()) / 1000.0  # mA -> A
            msg.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_UNKNOWN
            msg.present = True
            self.publisher_.publish(msg)
        except DeviceRangeError as e:
            self.get_logger().warn(f'Akım aralığı aşıldı: {e}')


def main(args=None):
    rclpy.init(args=args)
    node = Ina219Node()
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
