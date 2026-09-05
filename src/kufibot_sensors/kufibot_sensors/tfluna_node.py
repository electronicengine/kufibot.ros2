#!/usr/bin/env python3
import serial
import time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Range


class TfLunaNode(Node):
    def __init__(self):
        super().__init__('tfluna_node')

        self.declare_parameter('serial_port', '/dev/ttyAMA0')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('frame_id', 'lidar_link')
        self.declare_parameter('field_of_view_rad', 0.05)  # TF-Luna ~2 derece
        self.declare_parameter('min_range_m', 0.2)
        self.declare_parameter('max_range_m', 8.0)

        port = self.get_parameter('serial_port').value
        baud = self.get_parameter('baud_rate').value
        self.frame_id = self.get_parameter('frame_id').value
        self.fov = self.get_parameter('field_of_view_rad').value
        self.min_range = self.get_parameter('min_range_m').value
        self.max_range = self.get_parameter('max_range_m').value

        try:
            self.ser = serial.Serial(port, baud, timeout=0)
            if not self.ser.is_open:
                self.ser.open()
        except serial.SerialException as e:
            self.get_logger().error(f'Seri port açılamadı: {e}')
            raise

        self.range_pub = self.create_publisher(Range, 'lidar/range', 10)

        # Sensör kendi hızında veri gönderiyor (varsayılan 100Hz),
        # biz burada gelen veriyi hızlıca yokluyoruz (poll)
        self.timer = self.create_timer(0.01, self.poll_serial)
        self.get_logger().info(f'TF-Luna başlatıldı ({port} @ {baud} baud).')

    def poll_serial(self):
        # Bekleyen tüm baytları oku, en son geçerli paketi işle
        waiting = self.ser.in_waiting
        if waiting < 9:
            return

        data = self.ser.read(waiting)
        self.ser.reset_input_buffer()

        # Buffer içinde birden fazla paket olabilir, son geçerli olanı bul
        for i in range(len(data) - 8):
            if data[i] == 0x59 and data[i + 1] == 0x59:
                frame = data[i:i + 9]
                if len(frame) < 9:
                    continue
                distance_cm = frame[2] + frame[3] * 256
                strength = frame[4] + frame[5] * 256
                temp_raw = frame[6] + frame[7] * 256
                temperature_c = (temp_raw / 8.0) - 256.0

                self._publish_range(distance_cm / 100.0, strength, temperature_c)

    def _publish_range(self, distance_m, strength, temperature_c):
        # Düşük sinyal gücü = güvenilmez okuma (TF-Luna manueline göre)
        if strength < 100 or strength > 30000:
            self.get_logger().debug(f'Zayıf sinyal: {strength}')
            return

        msg = Range()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.radiation_type = Range.INFRARED
        msg.field_of_view = self.fov
        msg.min_range = self.min_range
        msg.max_range = self.max_range
        msg.range = distance_m
        self.range_pub.publish(msg)

    def destroy_node(self):
        if hasattr(self, 'ser') and self.ser.is_open:
            self.ser.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = TfLunaNode()
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
