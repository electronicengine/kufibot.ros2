"""Independent camera safety inference, also active while navigation owns the head."""
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String

from .obstacle_detector import collision_cues


class ObstacleNode(Node):
    def __init__(self):
        super().__init__('obstacle_detector_node')
        model = Path(get_package_share_directory('kufibot_perception')) / 'models/efficientdet_lite0.tflite'
        defaults = dict(model_path=str(model), inference_fps=5., score_threshold=.45,
                        corridor_left=.25, corridor_right=.75, near_bottom=.70,
                        lidar_image_x=.5, lidar_image_y=.5, max_frame_age_sec=1.)
        for name, value in defaults.items():
            self.declare_parameter(name, value)
        self.settings = {name: self.get_parameter(name).value for name in defaults}
        if (self.settings['inference_fps'] <= 0 or self.settings['max_frame_age_sec'] <= 0
                or not 0 <= self.settings['corridor_left'] < self.settings['corridor_right'] <= 1
                or not all(0 <= self.settings[k] <= 1 for k in
                           ('near_bottom', 'score_threshold', 'lidar_image_x', 'lidar_image_y'))):
            raise ValueError('invalid obstacle detector configuration')
        # Avoid PortAudio probing from the optional MediaPipe audio import.
        sys.modules['sounddevice'] = None
        os.environ.setdefault('MPLCONFIGDIR', '/tmp/kufibot-matplotlib')
        import mediapipe as mp
        self.mp = mp
        self.detector = mp.tasks.vision.ObjectDetector.create_from_options(
            mp.tasks.vision.ObjectDetectorOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=self.settings['model_path']),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                score_threshold=self.settings['score_threshold'], max_results=20))
        self.publisher = self.create_publisher(String, 'perception/navigation_obstacles', 1)
        self.latest = None
        self.last_timestamp_ms = -1
        self.create_subscription(Image, 'camera/image_raw', self._image, qos_profile_sensor_data)
        self.create_timer(1./self.settings['inference_fps'], self._detect)

    def _image(self, msg):
        self.latest = msg  # bounded newest-frame mailbox, no inference backlog

    def _detect(self):
        msg, self.latest = self.latest, None
        if msg is None:
            return  # silence is stale, never a synthetic clear result
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec/1e9
        age = self.get_clock().now().nanoseconds/1e9-stamp
        timestamp_ms = int(stamp*1000)
        if (not 0 <= age <= self.settings['max_frame_age_sec']
                or timestamp_ms <= self.last_timestamp_ms
                or msg.encoding not in ('rgb8', 'bgr8') or min(msg.width, msg.height) <= 0
                or msg.step < msg.width*3 or len(msg.data) != msg.step*msg.height):
            return
        self.last_timestamp_ms = timestamp_ms
        frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
        frame = frame[:, :msg.width*3].reshape(msg.height, msg.width, 3)
        if msg.encoding == 'bgr8':
            frame = frame[:, :, ::-1]
        started = time.monotonic()
        try:
            result = self.detector.detect_for_video(
                self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame)), timestamp_ms)
            objects = []
            for item in result.detections:
                if not item.categories:
                    continue
                category, box = item.categories[0], item.bounding_box
                objects.append(dict(label=category.category_name, confidence=float(category.score),
                    box=[box.origin_x/msg.width, box.origin_y/msg.height,
                         box.width/msg.width, box.height/msg.height]))
            data = collision_cues(objects, **{k: self.settings[k] for k in
                                             ('corridor_left', 'corridor_right', 'near_bottom',
                                              'lidar_image_x', 'lidar_image_y')})
            data.update(image_stamp_sec=stamp, inference_sec=time.monotonic()-started)
            self.publisher.publish(String(data=json.dumps(data, allow_nan=False)))
        except Exception as error:
            self.get_logger().error(f'Obstacle inference failed: {error}')
            # Navigation stops on stale output; failure must not report clear.

    def destroy_node(self):
        self.detector.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ObstacleNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
