#!/usr/bin/env python3
"""Publish frames from an OpenCV-compatible USB camera."""

from glob import glob
import os
from pathlib import Path
import time

from kufibot_interaction.local_voice_runtime import attach_phase_lease

import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Bool


class UsbCameraNode(Node):
    def __init__(self):
        super().__init__('usb_camera_node')
        self.declare_parameter('device', 'auto')
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 15.0)
        self.declare_parameter('use_mjpeg', True)
        self.declare_parameter('buffer_size', 1)
        self.declare_parameter('frame_id', 'camera_link')
        self.declare_parameter('reconnect_interval_sec', 2.0)
        self.declare_parameter('max_consecutive_failures', 5)
        self.requested_device = str(self.get_parameter('device').value)
        self.width = int(self.get_parameter('width').value)
        self.height = int(self.get_parameter('height').value)
        fps = float(self.get_parameter('fps').value)
        self.frame_id = str(self.get_parameter('frame_id').value)
        self.reconnect_interval = float(self.get_parameter(
            'reconnect_interval_sec').value)
        self.max_failures = int(self.get_parameter(
            'max_consecutive_failures').value)
        self.use_mjpeg = bool(self.get_parameter('use_mjpeg').value)
        self.buffer_size = max(1, int(self.get_parameter('buffer_size').value))
        self.capture = None
        self.active_device = None
        self.consecutive_failures = 0
        self.last_connect_attempt = 0.0
        self.last_no_camera_log = 0.0
        self.local_compute_active = False
        self.image_pub = self.create_publisher(Image, 'camera/image_raw', 5)
        self.info_pub = self.create_publisher(CameraInfo, 'camera/camera_info', 5)
        self.local_phase_lease = attach_phase_lease(
            self, lambda active: self._local_compute(Bool(data=active)))
        self._connect_camera()
        self.create_timer(1.0 / fps, self._capture)

    def _local_compute(self, msg):
        active = bool(msg.data)
        if active == self.local_compute_active:
            return
        self.local_compute_active = active
        self.get_logger().info(
            'Camera paused for Local AI inference' if active
            else 'Camera resumed after Local AI inference')

    def _candidate_devices(self):
        candidates = []
        if self.requested_device and self.requested_device != 'auto':
            candidates.append(self.requested_device)
        # by-id paths survive /dev/videoN renumbering after a USB reconnect.
        candidates.extend(sorted(glob('/dev/v4l/by-id/*')))
        # Raspberry Pi exposes hardware codecs as /dev/video10..31. Probing
        # those with OpenCV may block for 10 seconds each, so only inspect
        # video nodes whose sysfs device path belongs to USB.
        candidates.extend(
            device for device in sorted(glob('/dev/video*'))
            if self._is_usb_video_node(device))
        unique = []
        seen = set()
        for device in candidates:
            real_device = os.path.realpath(device)
            if real_device not in seen:
                unique.append(device)
                seen.add(real_device)
        return unique

    @staticmethod
    def _is_usb_video_node(device):
        name = Path(device).name
        sysfs_device = Path('/sys/class/video4linux') / name / 'device'
        try:
            return '/usb' in str(sysfs_device.resolve())
        except OSError:
            return False

    def _configure(self, capture):
        # A one-frame V4L2 queue is crucial for teleoperation: when a client
        # is briefly busy, resume from the newest camera frame rather than a
        # backlog. MJPEG also cuts USB bandwidth for common UVC webcams.
        capture.set(cv2.CAP_PROP_BUFFERSIZE, self.buffer_size)
        if self.use_mjpeg:
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, float(self.get_parameter('fps').value))

    def _connect_camera(self):
        self.last_connect_attempt = time.monotonic()
        for device in self._candidate_devices():
            capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not capture.isOpened():
                capture.release()
                continue
            self._configure(capture)
            # Some USB devices expose multiple video nodes; accept only one
            # that can actually return an image.
            ok, frame = capture.read()
            if not ok or frame is None:
                capture.release()
                continue
            self.capture = capture
            self.active_device = device
            self.consecutive_failures = 0
            self.get_logger().info(
                f'USB camera connected: {device} '
                f'({frame.shape[1]}x{frame.shape[0]})')
            self._publish(frame)
            return True
        self.capture = None
        self.active_device = None
        now = time.monotonic()
        if now - self.last_no_camera_log >= 10.0:
            self.get_logger().error(
                'No working USB V4L2 camera found; will retry automatically')
            self.last_no_camera_log = now
        return False

    def _disconnect_camera(self):
        if self.capture is not None:
            self.capture.release()
        self.capture = None
        self.active_device = None

    def _capture(self):
        # Keep the V4L2 device open so resume is instantaneous, but skip the
        # expensive capture, conversion, and ROS image publication work.
        if getattr(self, 'local_compute_active', False):
            return
        if self.capture is None:
            if time.monotonic() - self.last_connect_attempt >= self.reconnect_interval:
                self._connect_camera()
            return
        ok, frame = self.capture.read()
        if not ok:
            self.consecutive_failures += 1
            if self.consecutive_failures == 1:
                self.get_logger().warning(
                    f'Frame capture failed on {self.active_device}')
            if self.consecutive_failures >= self.max_failures:
                self.get_logger().error(
                    'Camera stopped responding; releasing it and scanning '
                    'for a re-enumerated device')
                self._disconnect_camera()
                self.last_connect_attempt = 0.0
            return
        self.consecutive_failures = 0
        self._publish(frame)

    def _publish(self, frame):
        stamp = self.get_clock().now().to_msg()
        image = Image()
        image.header.stamp = stamp
        image.header.frame_id = self.frame_id
        image.height = frame.shape[0]
        image.width = frame.shape[1]
        image.encoding = 'bgr8'
        image.is_bigendian = False
        image.step = frame.strides[0]
        image.data = frame.tobytes()
        info = CameraInfo()
        info.header = image.header
        info.width = frame.shape[1]
        info.height = frame.shape[0]
        self.image_pub.publish(image)
        self.info_pub.publish(info)

    def destroy_node(self):
        self._disconnect_camera()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = UsbCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
