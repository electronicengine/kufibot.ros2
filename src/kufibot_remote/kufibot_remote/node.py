"""ROS 2 bridge for an Expo Android LAN controller."""
import asyncio
import json
import math
import time
from collections import OrderedDict

from aiohttp import web
import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, Image, JointState, Range
from std_msgs.msg import Bool, Float32, String
from geometry_msgs.msg import Twist

from kufibot_interaction.local_voice_runtime import attach_phase_lease
from kufibot_interfaces.msg import VoiceState, Transcript
from .control import Control
from .server import Discovery, Server
from .video import LatestCameraTrack
from kufibot_interaction.navigation_tools import NavigationTools


class ToolSession:
    def __init__(self):
        self.handlers = {}

    def tool(self, **_metadata):
        def register(function):
            self.handlers[function.__name__] = function
            return function
        return register


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
        from kufibot_interaction.mimics import default_store
        for parameter in ('gesture_config_file', 'motion_config_file', 'joint_angles_file'):
            self.declare_parameter(parameter, '')
        self.control.mimic_store = default_store(*[
            self.get_parameter(parameter).value or None
            for parameter in ('gesture_config_file', 'motion_config_file', 'joint_angles_file')])
        self.session = ToolSession()
        self.session_active, self.stopping, self.tool_active = True, False, False
        self.ai_settings = {'provider': 'verasist'}
        self.navigation_camera_lock = asyncio.Lock()
        self.tool_images = OrderedDict()
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
        self.voice_transcripts = []
        self.transcript_pending = {}
        self.local_compute_active = False
        self.distance_map = None
        self.ai_settings_pub = self.create_publisher(String, 'voice_session/set_ai_settings', 10)
        self.create_subscription(String, 'voice_session/ai_settings', self._ai_settings, 10)
        self.create_subscription(VoiceState, 'voice_session/state', self._voice_state, 10)
        self.create_subscription(Transcript, 'voice_session/transcript', self._transcript, 50)
        self.local_phase_lease = attach_phase_lease(
            self, lambda active: self._local_compute(Bool(data=active)))
        self.remote_pub = self.create_publisher(String, 'remote/command', 10)
        self.drive_pub = self.create_publisher(Twist, 'drive/manual_cmd', 1)
        self.navigation = None
        self.navigation_at = 0.0
        self.create_subscription(String, 'navigation/distance_map', self._receive_distance_map, 1)
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
        self.tools = NavigationTools(self)
        self.tools.register(self.session)
        self.tools.connected = True

    async def _send_device_image(self, session, *, image_bytes, **_kwargs):
        image_id = f'{int(time.monotonic() * 1000)}'
        self.tool_images[image_id] = bytes(image_bytes)
        self.tool_last_image = image_id
        while len(self.tool_images) > 12:
            self.tool_images.popitem(last=False)
        return {'status': 'success'}

    async def run_tool(self, name, arguments):
        if self.control.mode != 'tools' or self.control.owner is None:
            raise ValueError('Araç çağrısı modu ve kumanda sahipliği gerekli')
        handler = self.session.handlers.get(name)
        if handler is None or not isinstance(arguments, dict):
            raise ValueError('Geçersiz araç çağrısı')
        self.tool_last_image = ''
        self.ai_settings = {'provider': 'verasist'}
        self.control.navigation_provider = 'verasist'
        # The UI may issue its call immediately after the mode acknowledgement.
        # Wait for the arbiter heartbeat instead of making the button appear
        # unusable while the mode transition takes one control tick.
        deadline = time.monotonic() + 2.0
        while self.applied_mode != 'tools' and time.monotonic() < deadline:
            await asyncio.sleep(.02)
        if self.applied_mode != 'tools':
            raise ValueError('Araç çağrısı modu servo denetleyicisinde uygulanmadı')
        self.tool_active = True
        self.tool_epoch = self.control.navigation_epoch
        try:
            # `navigation/session` is published at 10 Hz and authority at the
            # remote control tick.  Do not race the first task service call
            # against those two ROS messages after a tools-mode transition.
            deadline = time.monotonic() + 2.0
            while (not self.tools.state.get('enabled') or self.tools.state.get('session_id') != self.tools.session_id
                   and time.monotonic() < deadline):
                await asyncio.sleep(.02)
            if not self.tools.state.get('enabled') or self.tools.state.get('session_id') != self.tools.session_id:
                raise ValueError('Navigasyon oturumu hazırlanamadı')
            result = await handler(**arguments)
            if getattr(self, 'tool_last_image', ''):
                result['image_url'] = '/tool-image/' + self.tool_last_image
            return result
        finally:
            # Keep the task/map between calls; epoch changes revoke it on stop.
            pass

    def start_tick_timer(self, period_sec=.05):
        """Run control output from an executor that is owned by another host."""
        if not hasattr(self, '_control_timer'):
            self._control_timer = self.create_timer(period_sec, lambda: self.tick(period_sec))

    def _receive_distance_map(self, msg):
        try:
            data = json.loads(msg.data)
            if isinstance(data, dict) and data.get('frame') == 'startup_robot_pose' and data.get('map_id'):
                self.distance_map = data
        except (ValueError, TypeError):
            pass

    def _distance_map(self):
        return getattr(self, 'distance_map', None)

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
                self.control.disable_navigation('provider_changed')
            self.control.navigation_provider = provider
        except ValueError:
            self.get_logger().warning('Invalid AI settings status')

    def _transcript(self, msg):
        if msg.role not in ('user', 'assistant'):
            return
        pending = getattr(self, 'transcript_pending', {})
        key = pending.get(msg.role) or str(time.time_ns())
        if msg.final:
            pending.pop(msg.role, None)
        else:
            pending[msg.role] = key
        self.transcript_pending = pending
        entry = {'id': key, 'role': msg.role, 'text': msg.text[:12000], 'final': msg.final,
                 'timestamp_ms': int(key) / 1_000_000,
                 'workflow_id': (getattr(self, 'ai_config', None) or {}).get('settings', {}).get('workflow_id', '')}
        entries = list(getattr(self, 'voice_transcripts', []))
        index = next((i for i, item in enumerate(entries) if item['id'] == key), None)
        if index is None:
            if not msg.text:
                return
            entries.append(entry)
        else:
            entries[index] = entry
        self.voice_transcripts = entries[-64:]

    def _voice_state(self, msg):
        if self.control.mode != 'tools' and (not msg.session_active or msg.state != 'connected'):
            self.control.disable_navigation('voice_' + msg.state)
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
                'voiceTranscripts': getattr(self, 'voice_transcripts', []),
                'distanceMap': self._distance_map(),
                'sensors': {name: value if now - stamp < 3 else None
                            for name, (value, stamp) in self.sensors.items()}}

    def tick(self, dt):
        voice = getattr(self, 'voice_status', None) or {}
        now = time.monotonic()
        readiness_reason = next((reason for valid, reason in (
            (voice.get('active') is True, 'voice_inactive'),
            (voice.get('state') == 'connected', 'voice_not_connected'),
            (now - getattr(self, 'navigation_at', 0) < .5, 'navigation_state_stale'),
            (self.applied_mode in ('ai', 'tools'), 'applied_mode_not_ai'),
            (now - self.mode_time < .5, 'applied_mode_stale'),
        ) if not valid), '')
        self.control.navigation_ready = not readiness_reason
        linear, angular = self.control.tick(dt, self.current)
        if self.control.navigation_enabled and not self.control.navigation_ready:
            self.control.disable_navigation(readiness_reason)
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
        ready = (self.applied_mode in ('remote', 'ai', 'tools') and
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
                'enabled': self.control.navigation_enabled or (self.control.mode == 'tools' and self.tool_active
                    and getattr(self, 'tool_epoch', None) == self.control.navigation_epoch),
                'provider': self.control.navigation_provider, 'epoch': self.control.navigation_epoch,
                'reason': self.control.navigation_reason})))
        # Publish stop also on AI transition and on disconnect.
        drive = Twist()
        drive.linear.x, drive.angular.z = linear, angular
        self.drive_pub.publish(drive)


async def serve(node):
    server = Server(node.control, node.status,
                    lambda: LatestCameraTrack(
                        lambda: node.video_frame if time.monotonic() - node.video_frame_time < 2 else None,
                        node.get_parameter('video_fps').value), node.run_tool,
                    lambda image_id: node.tool_images.get(image_id))
    runner = web.AppRunner(server.app)
    await runner.setup()
    transport = None
    try:
        port = node.get_parameter('port').value
        await web.TCPSite(runner, '0.0.0.0', port).start()
        try:
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: Discovery(port, node.get_parameter('robot_name').value),
                local_addr=('0.0.0.0', node.get_parameter('discovery_port').value))
            discovery = 'UDP discovery enabled'
        except OSError as error:
            # Discovery is optional. A second simulator or a real robot may
            # already own UDP/8888; that must not take down HTTP/WebSocket
            # control for a browser opened at the known address.
            discovery = f'UDP discovery unavailable ({error}); direct URL works'
            node.get_logger().warning(discovery)
        node.get_logger().info(f'Web/mobile controller: http://<robot-ip>:{port}/ ({discovery})')
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
