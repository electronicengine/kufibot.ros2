#!/usr/bin/env python3
import math
import time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import MagneticField
from std_msgs.msg import Float32
from smbus2 import SMBus

HMC5883L_ADDRESS = 0x1E
REG_CONFIG_A = 0x00
REG_CONFIG_B = 0x01
REG_MODE = 0x02
REG_X_MSB = 0x03
REG_Z_MSB = 0x05
REG_Y_MSB = 0x07

GAUSS_PER_LSB = 1.0 / 1090.0   # config B = 0x20 -> 1.3 Ga aralığı, varsayılan gain
GAUSS_TO_TESLA = 1e-4

# C++ koduna göre kalibrasyon değerlerin - kendi ortamına göre yeniden kalibre etmen gerekebilir
OFFSET_X = -168.5
OFFSET_Y = -1227.0
SCALE_X = 0.981647
SCALE_Y = 1.019052
DECLINATION_RAD = 0.22  # ~13 derece, kendi konumuna göre ayarla


def to_signed16(value):
    if value >= 0x8000:
        value -= 0x10000
    return value


class Hmc5883lNode(Node):
    def __init__(self):
        super().__init__('hmc5883l_node')

        self.declare_parameter('i2c_bus', 1)
        self.declare_parameter('i2c_address', HMC5883L_ADDRESS)
        self.declare_parameter('publish_rate_hz', 15.0)
        self.declare_parameter('declination_rad', DECLINATION_RAD)
        self.declare_parameter('frame_id', 'compass_link')

        bus_num = self.get_parameter('i2c_bus').value
        self.address = self.get_parameter('i2c_address').value
        rate = self.get_parameter('publish_rate_hz').value
        self.declination = self.get_parameter('declination_rad').value
        self.frame_id = self.get_parameter('frame_id').value

        self.bus = SMBus(bus_num)
        self._configure_sensor()

        self.mag_pub = self.create_publisher(MagneticField, 'compass/mag', 10)
        self.heading_pub = self.create_publisher(Float32, 'compass/heading_deg', 10)

        self.timer = self.create_timer(1.0 / rate, self.timer_callback)
        self.get_logger().info('HMC5883L sensörü başlatıldı.')

    def _configure_sensor(self):
        self.bus.write_byte_data(self.address, REG_CONFIG_A, 0x70)  # 8 örnek @ 15Hz
        self.bus.write_byte_data(self.address, REG_CONFIG_B, 0x20)  # 1.3 Ga gain
        self.bus.write_byte_data(self.address, REG_MODE, 0x00)      # sürekli ölçüm
        time.sleep(0.01)

    def _read_word(self, reg):
        high = self.bus.read_byte_data(self.address, reg)
        low = self.bus.read_byte_data(self.address, reg + 1)
        return to_signed16((high << 8) | low)

    def timer_callback(self):
        try:
            raw_x = self._read_word(REG_X_MSB)
            raw_z = self._read_word(REG_Z_MSB)
            raw_y = self._read_word(REG_Y_MSB)
        except OSError as e:
            self.get_logger().warn(f'I2C okuma hatası: {e}')
            return

        cal_x = (raw_x - OFFSET_X) * SCALE_X
        cal_y = (raw_y - OFFSET_Y) * SCALE_Y
        cal_z = float(raw_z)

        heading_deg = self._compute_heading(cal_x, cal_y)

        mag_msg = MagneticField()
        mag_msg.header.stamp = self.get_clock().now().to_msg()
        mag_msg.header.frame_id = self.frame_id
        mag_msg.magnetic_field.x = cal_x * GAUSS_PER_LSB * GAUSS_TO_TESLA
        mag_msg.magnetic_field.y = cal_y * GAUSS_PER_LSB * GAUSS_TO_TESLA
        mag_msg.magnetic_field.z = cal_z * GAUSS_PER_LSB * GAUSS_TO_TESLA
        self.mag_pub.publish(mag_msg)

        heading_msg = Float32()
        heading_msg.data = heading_deg
        self.heading_pub.publish(heading_msg)

    def _compute_heading(self, x, y):
        heading_rad = math.atan2(y, x) + self.declination
        if heading_rad < 0:
            heading_rad += 2 * math.pi
        if heading_rad > 2 * math.pi:
            heading_rad -= 2 * math.pi
        return math.degrees(heading_rad)

    def destroy_node(self):
        self.bus.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = Hmc5883lNode()
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
