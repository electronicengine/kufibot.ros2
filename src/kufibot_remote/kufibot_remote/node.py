"""ROS 2 bridge for an Expo Android LAN controller."""
import asyncio
import json
import math
import time

from aiohttp import web
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, Image, JointState, Range
from std_msgs.msg import Bool, Float32, String
from geometry_msgs.msg import Twist

from kufibot_interfaces.msg import VoiceState
from .control import Control
from .server import Discovery, Server
from .video import LatestCameraTrack


class RemoteController(Node):
    def __init__(self):
        super().__init__('remote_controller')
        self.declare_parameter('port', 8080)
        self.declare_parameter('discovery_port', 8888)
        self.declare_parameter('robot_name', 'Kufibot')
        # The remote view is intentionally smaller/lighter than the camera
        # stream used by local perception.  Keeping this configurable lets a
        # fast LAN use a sharper image without making the default Pi profile
        # fall behind real time.
        self.declare_parameter('video_max_width', 480)
        self.declare_parameter('video_fps', 15.0)
        self.control = Control()
        self.current = {}
        self.sensors = {}
        self.video_frame = None
        self.video_frame_time = 0.0
        self.video_max_width = max(0, int(self.get_parameter('video_max_width').value))
        self.applied_mode = None
        self.mode_time = 0.0
        self.calibration = {'active': False, 'samples': 0, 'target': 500,
                    'message': 'Pusula sensörü bekleniyor'}
        self.ai_trigger_uuid = ''
        self.ai_config = None
        self.voice_status = None
        self.local_compute_active = False
        self.ai_settings_pub = self.create_publisher(String, 'voice_session/set_ai_settings', 10)
        self.create_subscription(String, 'voice_session/ai_settings', self._ai_settings, 10)
        self.create_subscription(VoiceState, 'voice_session/state', self._voice_state, 10)
        self.create_subscription(Bool, 'local_ai/compute_active', self._local_compute, 10)
        self.remote_pub = self.create_publisher(String, 'remote/command', 10)
        self.drive_pub = self.create_publisher(Twist, 'drive/manual_cmd', 1)
        self.navigation = None
        self.navigation_at = 0.0
        self.navigation_pub = self.create_publisher(String, 'navigation/authority', 1)
        self.create_subscription(String, 'navigation/state', self._navigation_state, 1)
        self.calibration_pub = self.create_publisher(String, 'compass/calibration_command', 10)
        self.ai_trigger_pub = self.create_publisher(String, 'voice_session/trigger_uuid', 10)
        self.create_subscription(String, 'remote/applied_mode', self._mode, 10)
        self.create_subscription(JointState, 'servo/joint_states', self._joints, 10)
        self.create_subscription(Image, 'camera/image_raw', self._image,
                                 QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.create_subscription(BatteryState, 'battery_state', self._battery,
                                 qos_profile_sensor_data)
        self.create_subscription(Range, 'lidar/range',
                                 lambda m: self._sensor('distance', m.range),
                                 qos_profile_sensor_data)
        self.create_subscription(Float32, 'compass/heading_deg',
                                 lambda m: self._sensor('heading', m.data),
                                 qos_profile_sensor_data)
        self.create_subscription(String, 'compass/calibration_status',
                                 self._calibration_status, 10)
        self.create_subscription(String, 'voice_session/trigger_uuid',
                                 self._ai_trigger_uuid, 10)

    def _navigation_state(self, msg):
        try:
            value = json.loads(msg.data)
            if not isinstance(value, dict):
                return
            self.navigation, self.navigation_at = value, time.monotonic()
        except (ValueError, TypeError):
            return

    def _ai_settings(self, msg):
        try:
            self.ai_config = json.loads(msg.data)
            provider = self.ai_config.get('settings', {}).get('provider')
            if provider != self.control.navigation_provider:
                self.control.disable_navigation()
            self.control.navigation_provider = provider
        except ValueError:
            self.get_logger().warning('Invalid AI settings status')

    def _voice_state(self, msg):
        if not msg.session_active or msg.state != 'connected':
            self.control.disable_navigation()
        self.voice_status = {'state': msg.state, 'detail': msg.detail,
                             'active': msg.session_active}

    def _local_compute(self, msg):
        self.local_compute_active = bool(msg.data)
        if self.local_compute_active:
            self.video_frame = None
            self.video_frame_time = 0.0

    def _ai_trigger_uuid(self, msg):
        self.ai_trigger_uuid = msg.data

    def _calibration_status(self, msg):
        try:
            status = json.loads(msg.data)
            if (isinstance(status, dict) and isinstance(status.get('active'), bool)
                    and isinstance(status.get('samples'), int) and isinstance(status.get('target'), int)
                    and isinstance(status.get('message'), str)):
                for name in ('raw', 'minimum', 'maximum'):
                    values = status.get(name)
                    if values is not None and (not isinstance(values, dict)
                                               or not all(isinstance(values.get(axis), (int, float))
                                                          for axis in ('x', 'y'))):
                        return
                self.calibration = status
        except (TypeError, ValueError):
            self.get_logger().warning('Invalid compass calibration status')

    def _mode(self, msg):
        self.applied_mode = msg.data
        self.mode_time = time.monotonic()

    def _sensor(self, name, value):
        self.sensors[name] = (value if math.isfinite(value) else None,
                              time.monotonic())

    def _battery(self, msg):
        self._sensor('voltage', msg.voltage)
        self._sensor('current', msg.current)

    def _joints(self, msg):
        if len(msg.name) == len(msg.position):
            self.current = {n: math.degrees(v) for n, v in
                            zip(msg.name, msg.position) if math.isfinite(v)}

    def _image(self, msg):
        if getattr(self, 'local_compute_active', False):
            return
        now = time.monotonic()
        if msg.encoding not in ('bgr8', 'rgb8'):
            return
        try:
            frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                msg.height, msg.step)[:, :msg.width * 3].reshape(
                    msg.height, msg.width, 3)
            if msg.encoding == 'rgb8':
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            max_width = self.video_max_width
            if max_width and msg.width > max_width:
                frame = cv2.resize(frame, (max_width,
                    max(1, round(msg.height * max_width / msg.width))),
                    interpolation=cv2.INTER_AREA)
            self.video_frame = frame.copy()
            self.video_frame_time = now
        except (ValueError, cv2.error):
            self.get_logger().warning('Invalid camera frame')

    def status(self):
        now = time.monotonic()
        applied = self.applied_mode if now - self.mode_time < 1 else None
        navigation = getattr(self, 'navigation', None) if now - getattr(self, 'navigation_at', 0) < .5 else None
        return {'navigation': navigation, 'navigationRequested': self.control.navigation_enabled,
                'version': 1, 'mode': self.control.mode, 'appliedMode': applied,
                'camera': (not getattr(self, 'local_compute_active', False)
                           and now - self.video_frame_time < 2),
                'driveAvailable': bool(navigation and navigation.get('motor_available')),
                'joints': self.current,
                'calibration': self.calibration,
                'aiTriggerUuid': self.ai_trigger_uuid,
                'aiConfig': getattr(self, 'ai_config', None),
                'voiceStatus': getattr(self, 'voice_status', None),
                'sensors': {name: value if now - stamp < 3 else None
                            for name, (value, stamp) in self.sensors.items()}}

    def tick(self, dt):
        voice = getattr(self, 'voice_status', None) or {}
        self.control.navigation_ready = (voice.get('active') is True and voice.get('state') == 'connected'
            and time.monotonic() - getattr(self, 'navigation_at', 0) < .5
            and self.applied_mode == 'ai' and time.monotonic() - self.mode_time < .5)
        linear, angular = self.control.tick(dt, self.current)
        if self.control.navigation_enabled and not self.control.navigation_ready:
            self.control.disable_navigation()
        if self.control.calibration_requested:
            self.calibration = {'active': True, 'samples': 0, 'target': self.calibration['target'],
                                'message': 'Kalibrasyon başlatılıyor'}
            self.calibration_pub.publish(String(data='start'))
            self.control.calibration_requested = False
        if self.control.ai_settings_requested is not None:
            self.ai_settings_pub.publish(String(data=json.dumps(self.control.ai_settings_requested)))
            self.control.ai_settings_requested = None
        if self.control.ai_trigger_uuid:
            self.ai_trigger_pub.publish(String(data=self.control.ai_trigger_uuid))
            self.control.ai_trigger_uuid = None
        ready = (self.applied_mode == 'remote' and
                 time.monotonic() - self.mode_time < 1)
        if not ready:
            self.control.stop()
            linear = angular = 0.0
        msg = String()
        msg.data = json.dumps({'mode': self.control.mode,
                               'targets': self.control.targets})
        self.remote_pub.publish(msg)
        if hasattr(self, 'navigation_pub'):
            self.navigation_pub.publish(String(data=json.dumps({
                'mode': self.control.mode, 'owner': self.control.owner is not None,
                'enabled': self.control.navigation_enabled,
                'provider': self.control.navigation_provider, 'epoch': self.control.navigation_epoch})))
        # Publish stop also on AI transition and on disconnect.
        drive = Twist()
        drive.linear.x, drive.angular.z = linear, angular
        self.drive_pub.publish(drive)


async def serve(node):
    server = Server(node.control, node.status,
                    lambda: LatestCameraTrack(
                        lambda: node.video_frame if time.monotonic() - node.video_frame_time < 2 else None,
                        node.get_parameter('video_fps').value))
    runner = web.AppRunner(server.app)
    await runner.setup()
    transport = None
    try:
        port = node.get_parameter('port').value
        await web.TCPSite(runner, '0.0.0.0', port).start()
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: Discovery(port, node.get_parameter('robot_name').value),
            local_addr=('0.0.0.0', node.get_parameter('discovery_port').value))
        node.get_logger().info(f'Web/mobile controller: http://<robot-ip>:{port}/ (UDP discovery enabled)')
        previous = time.monotonic()
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0)
            now = time.monotonic()
            if now - previous >= 0.05:
                node.tick(now - previous)
                previous = now
            await asyncio.sleep(0.002)
    finally:
        node.control.stop()
        node.drive_pub.publish(Twist())
        if transport:
            transport.close()
        await server.close()
        await runner.cleanup()


def main(args=None):
    rclpy.init(args=args)
    node = RemoteController()
    try:
        asyncio.run(serve(node))
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
