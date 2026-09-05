#!/usr/bin/env python3
"""Detect faces/hands and publish C++-style polar head commands."""

import time
import sys
import os

import cv2
# MediaPipe imports its optional audio Tasks API at package import time. On
# headless Raspberry Pi, sounddevice can block while PortAudio probes devices.
# Vision tracking does not use it, and MediaPipe handles its absence.
sys.modules['sounddevice'] = None
os.environ.setdefault('MPLCONFIGDIR', '/tmp/kufibot-matplotlib')
import mediapipe as mp
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

from kufibot_interfaces.msg import (
    Detection, DetectionArray, JointCommand, Landmark, TrackingTarget)
from .tracking import select_largest
from .tracking_controller import PolarHeadTracker


class MediaPipeNode(Node):
    def __init__(self):
        super().__init__('mediapipe_node')
        self.declare_parameter('min_detection_confidence', 0.6)
        self.declare_parameter('target_timeout_sec', 0.25)
        self.declare_parameter('error_threshold_px', 70.0)
        self.declare_parameter('release_threshold_px', 85.0)
        self.declare_parameter('max_error_px', 300.0)
        self.declare_parameter('minimum_step_deg', 0.15)
        self.declare_parameter('maximum_step_deg', 2.5)
        self.declare_parameter('control_rate_hz', 20.0)
        self.declare_parameter('neutral_step_deg', 1.0)
        self.declare_parameter('publish_debug_image', False)
        confidence = float(self.get_parameter(
            'min_detection_confidence').value)
        self.timeout = float(self.get_parameter('target_timeout_sec').value)
        self.tracker = PolarHeadTracker(
            error_threshold_px=self.get_parameter('error_threshold_px').value,
            release_threshold_px=self.get_parameter(
                'release_threshold_px').value,
            max_error_px=self.get_parameter('max_error_px').value,
            minimum_step_deg=self.get_parameter('minimum_step_deg').value,
            maximum_step_deg=self.get_parameter('maximum_step_deg').value)
        self.neutral_step = float(self.get_parameter('neutral_step_deg').value)
        control_rate = float(self.get_parameter('control_rate_hz').value)
        if control_rate <= 0.0:
            raise ValueError('control_rate_hz must be positive')
        self.debug = bool(self.get_parameter('publish_debug_image').value)
        if not hasattr(mp, 'solutions'):
            raise RuntimeError('MediaPipe solutions API is unavailable')
        self.face = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=confidence)
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False, max_num_hands=2,
            min_detection_confidence=confidence,
            min_tracking_confidence=confidence)
        self.faces_pub = self.create_publisher(
            DetectionArray, 'perception/faces', 5)
        self.hands_pub = self.create_publisher(
            DetectionArray, 'perception/hands', 5)
        self.target_pub = self.create_publisher(
            TrackingTarget, 'perception/tracking_target', 5)
        self.command_pub = self.create_publisher(
            JointCommand, 'servo/tracking_targets', 5)
        self.debug_pub = self.create_publisher(
            Image, 'perception/debug_image', 2)
        self.create_subscription(Image, 'camera/image_raw', self._image, 5)
        self.latest_target = None
        self.image_size = (640, 480)
        self.last_target_time = 0.0
        self.create_timer(1.0 / control_rate, self._control_tick)

    @staticmethod
    def _landmark(index, point):
        msg = Landmark()
        msg.index = index
        msg.x = float(point.x)
        msg.y = float(point.y)
        # FaceDetection relative keypoints are 2D; hand landmarks include z.
        msg.z = float(getattr(point, 'z', 0.0))
        msg.visibility = float(getattr(point, 'visibility', 1.0))
        return msg

    def _image(self, image_msg):
        if image_msg.encoding not in ('bgr8', 'rgb8'):
            self.get_logger().warning(
                f'Unsupported camera encoding: {image_msg.encoding}')
            return
        frame = np.frombuffer(image_msg.data, dtype=np.uint8).reshape(
            image_msg.height, image_msg.step)[:, :image_msg.width * 3]
        frame = frame.reshape(image_msg.height, image_msg.width, 3).copy()
        if image_msg.encoding == 'rgb8':
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_result = self.face.process(rgb)
        hand_result = self.hands.process(rgb)
        faces = DetectionArray(header=image_msg.header)
        for index, item in enumerate(face_result.detections or []):
            box = item.location_data.relative_bounding_box
            det = Detection(id=f'face-{index}', kind='face')
            det.confidence = float(item.score[0])
            keypoints = item.location_data.relative_keypoints
            # As in the C++ tracker, follow the midpoint between both eyes.
            if len(keypoints) >= 2:
                det.center_x = float((keypoints[0].x + keypoints[1].x) / 2.0)
                det.center_y = float((keypoints[0].y + keypoints[1].y) / 2.0)
            else:
                det.center_x = float(box.xmin + box.width / 2.0)
                det.center_y = float(box.ymin + box.height / 2.0)
            det.width, det.height = float(box.width), float(box.height)
            det.landmarks = [
                self._landmark(i, point)
                for i, point in enumerate(item.location_data.relative_keypoints)
            ]
            faces.detections.append(det)
        hands = DetectionArray(header=image_msg.header)
        for index, points in enumerate(hand_result.multi_hand_landmarks or []):
            xs = [p.x for p in points.landmark]
            ys = [p.y for p in points.landmark]
            det = Detection(id=f'hand-{index}', kind='hand', confidence=1.0)
            det.center_x = float((min(xs) + max(xs)) / 2.0)
            det.center_y = float((min(ys) + max(ys)) / 2.0)
            det.width, det.height = float(max(xs) - min(xs)), float(max(ys) - min(ys))
            det.landmarks = [self._landmark(i, p) for i, p in enumerate(points.landmark)]
            hands.detections.append(det)
        self.faces_pub.publish(faces)
        self.hands_pub.publish(hands)
        selected = select_largest(faces.detections, hands.detections)
        self._publish_target(image_msg, selected)
        if self.debug:
            for det in faces.detections + hands.detections:
                height, width = frame.shape[:2]
                x1 = int((det.center_x - det.width / 2.0) * width)
                y1 = int((det.center_y - det.height / 2.0) * height)
                x2 = int((det.center_x + det.width / 2.0) * width)
                y2 = int((det.center_y + det.height / 2.0) * height)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            height, width = frame.shape[:2]
            radius = max(1, int(
                self.tracker.error_threshold_px
                * min(width / 640.0, height / 480.0)))
            cv2.circle(frame, (width // 2, height // 2), radius,
                       (255, 255, 0), 2)
            if selected is not None:
                center = (int(selected.center_x * width),
                          int(selected.center_y * height))
                cv2.drawMarker(frame, center, (0, 0, 255),
                               cv2.MARKER_CROSS, 18, 2)
            debug_msg = Image()
            debug_msg.header = image_msg.header
            debug_msg.height = frame.shape[0]
            debug_msg.width = frame.shape[1]
            debug_msg.encoding = 'bgr8'
            debug_msg.is_bigendian = False
            debug_msg.step = frame.strides[0]
            debug_msg.data = frame.tobytes()
            self.debug_pub.publish(debug_msg)

    def _publish_target(self, image_msg, detection):
        target = TrackingTarget(header=image_msg.header)
        now = time.monotonic()
        if detection is not None:
            point = (detection.center_x, detection.center_y)
            self.latest_target = point
            self.image_size = (image_msg.width, image_msg.height)
            self.last_target_time = now
            target.valid = True
            target.detection_id = detection.id
            target.kind = detection.kind
            target.x, target.y = point
            target.confidence = detection.confidence
        elif self.latest_target is not None and now - self.last_target_time <= self.timeout:
            target.valid = True
            target.detection_id = 'held-target'
            target.kind = 'held'
            target.x, target.y = self.latest_target
        else:
            target.valid = False
            self.latest_target = None
        self.target_pub.publish(target)

    def _control_tick(self):
        now = time.monotonic()
        if (self.latest_target is not None
                and now - self.last_target_time <= self.timeout):
            width, height = self.image_size
            head, neck, _ = self.tracker.update(
                self.latest_target[0], self.latest_target[1], width, height)
        else:
            head, neck = self.tracker.move_to_neutral(self.neutral_step)
        msg = JointCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.names = ['headLeftRight', 'neck']
        msg.angles_deg = [head, neck]
        self.command_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = MediaPipeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
