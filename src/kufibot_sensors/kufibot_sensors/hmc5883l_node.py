#!/usr/bin/env python3
import json
import math
import os
import time
from pathlib import Path
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import MagneticField
from std_msgs.msg import Float32, String
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
        self.declare_parameter('calibration_samples', 500)
        self.declare_parameter('calibration_file',
                       str(Path.home() / '.ros' / 'kufibot_hmc5883l_calibration.json'))

        bus_num = self.get_parameter('i2c_bus').value
        self.address = self.get_parameter('i2c_address').value
        rate = self.get_parameter('publish_rate_hz').value
        self.declination = self.get_parameter('declination_rad').value
        self.frame_id = self.get_parameter('frame_id').value
        self.calibration_samples = int(self.get_parameter('calibration_samples').value)
        self.calibration_file = Path(self.get_parameter('calibration_file').value).expanduser()
        self.offset_x, self.offset_y = OFFSET_X, OFFSET_Y
        self.scale_x, self.scale_y = SCALE_X, SCALE_Y
        self.calibration = None
        self._load_calibration()

        self.bus = SMBus(bus_num)
        self._configure_sensor()

        self.mag_pub = self.create_publisher(MagneticField, 'compass/mag', 10)
        self.heading_pub = self.create_publisher(Float32, 'compass/heading_deg', 10)
        self.calibration_pub = self.create_publisher(String, 'compass/calibration_status', 10)
        self.create_subscription(String, 'compass/calibration_command',
                     self._calibration_command, 10)

        self.timer = self.create_timer(1.0 / rate, self.timer_callback)
        self._publish_calibration_status('Hazır')
        self.get_logger().info('HMC5883L sensörü başlatıldı.')

    def _load_calibration(self):
        try:
            values = json.loads(self.calibration_file.read_text(encoding='utf-8'))
            self.offset_x = float(values['offset_x'])
            self.offset_y = float(values['offset_y'])
            self.scale_x = float(values['scale_x'])
            self.scale_y = float(values['scale_y'])
        except (OSError, ValueError, KeyError, TypeError):
            return
        self.get_logger().info(f'Pusula kalibrasyonu yüklendi: {self.calibration_file}')

    def _calibration_command(self, msg):
        if msg.data != 'start':
            return
        self.calibration = {'samples': 0, 'min_x': math.inf, 'max_x': -math.inf,
                            'min_y': math.inf, 'max_y': -math.inf}
        self._publish_calibration_status('Robotu yatay tutup yavaşça farklı yönlere çevirin')

    def _publish_calibration_status(self, message):
        active = self.calibration is not None
        samples = self.calibration['samples'] if active else 0
        status = {
            'active': active, 'samples': samples, 'target': self.calibration_samples,
            'message': message,
        }
        if active and samples:
            status['raw'] = {'x': self.calibration['raw_x'], 'y': self.calibration['raw_y']}
            status['minimum'] = {'x': self.calibration['min_x'], 'y': self.calibration['min_y']}
            status['maximum'] = {'x': self.calibration['max_x'], 'y': self.calibration['max_y']}
        self.calibration_pub.publish(String(data=json.dumps(status)))

    def _add_calibration_sample(self, raw_x, raw_y):
        calibration = self.calibration
        if calibration is None:
            return
        calibration['samples'] += 1
        calibration['raw_x'], calibration['raw_y'] = raw_x, raw_y
        calibration['min_x'] = min(calibration['min_x'], raw_x)
        calibration['max_x'] = max(calibration['max_x'], raw_x)
        calibration['min_y'] = min(calibration['min_y'], raw_y)
        calibration['max_y'] = max(calibration['max_y'], raw_y)
        if calibration['samples'] < self.calibration_samples:
            self._publish_calibration_status('Robotu yatay tutup yavaşça farklı yönlere çevirin')
            return
        span_x = calibration['max_x'] - calibration['min_x']
        span_y = calibration['max_y'] - calibration['min_y']
        if min(span_x, span_y) <= 0:
            self.calibration = None
            self._publish_calibration_status('Kalibrasyon başarısız: yeterli hareket algılanmadı')
            return
        self.offset_x = (calibration['max_x'] + calibration['min_x']) / 2
        self.offset_y = (calibration['max_y'] + calibration['min_y']) / 2
        average_span = (span_x + span_y) / 2
        self.scale_x, self.scale_y = average_span / span_x, average_span / span_y
        self.calibration = None
        try:
            self.calibration_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.calibration_file.with_suffix('.tmp')
            temporary.write_text(json.dumps({'offset_x': self.offset_x, 'offset_y': self.offset_y,
                                             'scale_x': self.scale_x, 'scale_y': self.scale_y}),
                                 encoding='utf-8')
            os.replace(temporary, self.calibration_file)
        except OSError as error:
            self.get_logger().error(f'Kalibrasyon kaydedilemedi: {error}')
            self._publish_calibration_status('Kalibrasyon uygulandı, ancak kaydedilemedi')
            return
        self._publish_calibration_status('Kalibrasyon tamamlandı ve kaydedildi')

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

        self._add_calibration_sample(raw_x, raw_y)
        cal_x = (raw_x - self.offset_x) * self.scale_x
        cal_y = (raw_y - self.offset_y) * self.scale_y
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
