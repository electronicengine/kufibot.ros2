"""ROS adapter for navigation; its executor never waits on the voice backend."""
import json
import math
import time

import cv2
import numpy as np
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from rclpy.task import Future
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Range, Image, CompressedImage, JointState
from std_msgs.msg import String
from kufibot_interfaces.action import NavigateStep
from kufibot_interfaces.srv import NavigationTask, GetObservation
from kufibot_interfaces.msg import JointCommand, DriveCommand
from .core import Config, Navigator


def json_data(msg):
    try:
        value = json.loads(msg.data)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


class NavigationNode(Node):
    def __init__(self):
        super().__init__('navigation_node')
        defaults = Config()
        config = {}
        for name, value in vars(defaults).items():
            self.declare_parameter(name, value)
            config[name] = self.get_parameter(name).value
        self.nav = Navigator(Config(**config))
        self.pending = {}
        self.reserved = False
        self.drive_pub = self.create_publisher(DriveCommand, 'drive/command', 1)
        self.velocity_pub = self.create_publisher(Twist, 'cmd_vel', 1)
        self.state_pub = self.create_publisher(String, 'navigation/state', 1)
        self.head_pub = self.create_publisher(JointCommand, 'servo/navigation_targets', 1)
        self.create_subscription(Range, 'lidar/range', self._range, qos_profile_sensor_data)
        self.create_subscription(String, 'navigation/authority', self._authority, 1)
        self.create_subscription(String, 'navigation/session', self._session, 1)
        self.create_subscription(String, 'remote/applied_mode', self._mode, 1)
        self.create_subscription(Twist, 'drive/manual_cmd', self._manual, 1)
        from std_msgs.msg import Float32
        self.create_subscription(Float32, 'compass/heading_deg', self._heading, qos_profile_sensor_data)
        self.create_subscription(JointState, 'servo/joint_states', self._joints, qos_profile_sensor_data)
        self.create_subscription(Image, 'camera/image_raw', self._image, qos_profile_sensor_data)
        self.create_service(NavigationTask, 'navigation/task', self._task)
        self.create_service(GetObservation, 'navigation/observation', self._observation)
        self.action = ActionServer(self, NavigateStep, 'navigation/step', self._execute,
                                   goal_callback=self._goal, cancel_callback=self._cancel)
        self.create_timer(.05, self._tick)

    def _authority(self, msg):
        self.nav.set_authority(json_data(msg))

    def _session(self, msg):
        data = json_data(msg)
        self.nav.set_session(str(data.get('session_id', '')), data.get('connected') is True)

    def _mode(self, msg):
        self.nav.applied_mode, self.nav.applied_at = msg.data, time.monotonic()

    def _manual(self, msg):
        linear, angular = msg.linear.x, msg.angular.z
        if math.isfinite(linear) and math.isfinite(angular):
            self.nav.manual = (linear, angular)
            self.nav.manual_at = time.monotonic()

    def _heading(self, msg):
        self.nav.sensor('heading', msg.data % 360 if math.isfinite(msg.data) else None)

    def _source_fresh(self, msg, max_age):
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        age = self.get_clock().now().nanoseconds / 1e9 - stamp
        return 0 <= age <= max_age

    def _range(self, msg):
        valid = (self._source_fresh(msg, self.nav.c.sensor_age_sec)
                 and all(math.isfinite(v) for v in (msg.range, msg.min_range, msg.max_range))
                 and 0 < msg.min_range < msg.max_range and msg.min_range <= msg.range <= msg.max_range)
        self.nav.sensor('range', msg.range if valid else None)
        if valid:
            self.nav.sensor('range_min', msg.min_range)

    def _joints(self, msg):
        joints = dict(zip(msg.name, (math.degrees(v) for v in msg.position)))
        valid = self._source_fresh(msg, self.nav.c.sensor_age_sec) and len(msg.name) == len(msg.position) and all(
            name in joints and math.isfinite(joints[name]) for name in ('neck', 'headLeftRight'))
        self.nav.sensor('joints', joints if valid else None)

    def _image(self, msg):
        valid = (self._source_fresh(msg, self.nav.c.camera_age_sec) and msg.encoding in ('rgb8', 'bgr8') and msg.width > 0 and msg.height > 0
                 and msg.step >= msg.width * 3 and len(msg.data) == msg.step * msg.height)
        self.nav.sensor('image', msg if valid else None)

    def _task(self, request, response):
        result = self.nav.task(request.operation, request.session_id, request.task_id,
                               request.request_id, request.label)
        response.result_json = json.dumps(result, allow_nan=False)
        return response

    @staticmethod
    def encode(msg):
        frame = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
        frame = frame[:, :msg.width * 3].reshape(msg.height, msg.width, 3)
        if msg.encoding == 'rgb8':
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if msg.width > 640:
            frame = cv2.resize(frame, (640, max(1, round(msg.height * 640 / msg.width))))
        ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise ValueError('JPEG encoding failed')
        return CompressedImage(header=msg.header, format='jpeg', data=jpeg.tobytes())

    def _observation(self, request, response):
        obs = self.nav.observations.get(request.observation_id)
        if (not self.nav.allowed() or request.session_id != self.nav.session_id
                or request.task_id != self.nav.task_id or not self.nav.task_id or not obs):
            response.result_json = json.dumps({'status': 'error', 'reason': 'observation_unavailable'})
            return response
        try:
            response.images = [self.encode(frame) for _, frame in obs['frames']]
            data = {key: value for key, value in obs.items() if key != 'frames'}
            data['images'] = [metadata for metadata, _ in obs['frames']]
            data['status'] = 'ok'
            response.result_json = json.dumps(data, allow_nan=False)
        except (ValueError, cv2.error) as error:
            response.result_json = json.dumps({'status': 'error', 'reason': str(error)})
        return response

    def _goal(self, request):
        if self.reserved or self.nav.step:
            return GoalResponse.REJECT
        if (not self.nav.allowed() or request.session_id != self.nav.session_id
                or not request.task_id or request.task_id != self.nav.task_id):
            return GoalResponse.REJECT
        self.reserved = True
        return GoalResponse.ACCEPT

    def _cancel(self, goal):
        if self.nav.step and self.nav.step['request_id'] == goal.request.request_id:
            self.nav._finish('cancelled', 'action_cancelled')
        return CancelResponse.ACCEPT

    async def _execute(self, goal):
        request = goal.request
        fields = ('session_id', 'task_id', 'request_id', 'operation', 'observation_id',
                  'distance_m', 'angle_deg', 'sweep_deg')
        result = self.nav.submit({key: getattr(request, key) for key in fields})
        if result is None:
            future = Future()
            self.pending[request.request_id] = (future, goal)
            result = await future
        self.reserved = False
        if goal.is_cancel_requested:
            goal.canceled()
        elif result.get('status') == 'ok':
            goal.succeed()
        else:
            goal.abort()
        return NavigateStep.Result(result_json=json.dumps(result))

    def _tick(self):
        linear, angular = self.nav.tick()
        profile = ('manual' if self.nav.authority.get('mode') == 'remote'
                   else 'navigation' if self.nav.enabled and self.nav.c.calibrated else 'stop')
        twist = Twist()
        twist.linear.x, twist.angular.z = float(linear), float(angular)
        command = DriveCommand(profile=profile, twist=twist)
        command.header.stamp = self.get_clock().now().to_msg()
        self.drive_pub.publish(command)
        self.velocity_pub.publish(twist)
        msg = JointCommand()
        msg.header.stamp = command.header.stamp
        if self.nav.enabled and self.nav.task_id and self.nav.head_target:
            msg.names = ['headLeftRight', 'neck']
            msg.angles_deg = list(self.nav.head_target)
            msg.hold_sec = .25
        else:
            msg.cancel_agent = True
        self.head_pub.publish(msg)
        state = self.nav.status()
        state['motor_available'] = self.drive_pub.get_subscription_count() > 0
        self.state_pub.publish(String(data=json.dumps(state)))
        for rid, (future, goal) in list(self.pending.items()):
            if goal.is_cancel_requested and self.nav.step and self.nav.step['request_id'] == rid:
                self.nav._finish('cancelled', 'action_cancelled')
            result = self.nav.results.get(rid)
            # Session changes intentionally erase old results, but must still
            # settle the old ROS action so another task can be accepted.
            if result is None and not self.nav.step:
                result = {'status': 'cancelled', 'reason': 'session_ended'}
            if result is not None:
                future.set_result(result)
                del self.pending[rid]
            else:
                goal.publish_feedback(NavigateStep.Feedback(state=self.nav.state, progress=0.0))

    def destroy_node(self):
        self.drive_pub.publish(DriveCommand(profile='stop'))
        self.velocity_pub.publish(Twist())
        self.head_pub.publish(JointCommand(cancel_agent=True))
        self.action.destroy()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = NavigationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
