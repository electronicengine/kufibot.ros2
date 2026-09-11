"""ROS world simulator: integrates pose, raycasts lidar/camera from a floor plan.

Ground truth only; navigation_node never sees this module directly, only the
lidar/compass/camera/joint_states topics it already consumes on real hardware.
"""
import json
import math
import random
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Range, Image, JointState, BatteryState
from std_msgs.msg import Float32, String

from kufibot_interaction.joint_limits import NEUTRAL_ANGLES, mapping_sensor_pose_valid
from kufibot_interaction.robot_model import load_rig

from . import renderer
from .floorplan import FloorPlan
from .sensor_geometry import SensorWorld, sensor_pose

DEFAULT_FLOORPLAN = str(Path(__file__).with_name('floorplans') / 'apartment_default.json')


class WorldNode(Node):
    def __init__(self):
        super().__init__('world_node')
        self.declare_parameter('floorplan_file', DEFAULT_FLOORPLAN)
        self.declare_parameter('body_radius_m', 0.16)
        self.declare_parameter('robot_width_m', 0.32)
        self.declare_parameter('robot_height_m', 0.32)
        self.declare_parameter('head_center_deg', 90.0)
        self.declare_parameter('head_sign', -1.0)
        self.declare_parameter('lidar_yaw_offset_deg', 0.0)
        self.declare_parameter('camera_yaw_offset_deg', 0.0)
        self.declare_parameter('lidar_min_range_m', 0.2)
        self.declare_parameter('lidar_max_range_m', 8.0)
        # Measurement error only: world pose/collision remain exact ground
        # truth, while navigation sees these noisy topic values.
        self.declare_parameter('sensor_noise_seed', 42)
        self.declare_parameter('lidar_noise_stddev_m', 0.0)
        self.declare_parameter('lidar_quantization_m', 0.0)
        self.declare_parameter('compass_noise_stddev_deg', 0.0)
        self.declare_parameter('camera_renderer', 'schematic')
        self.scene_camera = None
        self.declare_parameter('camera_width', 640)
        self.declare_parameter('camera_height', 480)
        self.declare_parameter('camera_fov_deg', 60.0)
        self.declare_parameter('twist_timeout_sec', 0.5)
        self.declare_parameter('wheel_separation_m', 0.2)

        self.floorplan_file = str(self.get_parameter('floorplan_file').value)
        self.floorplan = FloorPlan.load(self.floorplan_file)
        self.sensor_world = SensorWorld(self.floorplan)
        self.rig = load_rig()
        self.joints_deg = dict(NEUTRAL_ANGLES)
        self.body_radius = float(self.get_parameter('body_radius_m').value)
        self.robot_width = float(self.get_parameter('robot_width_m').value)
        self.robot_height = float(self.get_parameter('robot_height_m').value)
        self.head_center_deg = float(self.get_parameter('head_center_deg').value)
        self.head_sign = float(self.get_parameter('head_sign').value)
        self.lidar_yaw_offset_deg = float(self.get_parameter('lidar_yaw_offset_deg').value)
        self.camera_yaw_offset_deg = float(self.get_parameter('camera_yaw_offset_deg').value)
        self.lidar_min_range = float(self.get_parameter('lidar_min_range_m').value)
        self.lidar_max_range = float(self.get_parameter('lidar_max_range_m').value)
        self.lidar_noise_stddev = float(self.get_parameter('lidar_noise_stddev_m').value)
        self.lidar_quantization = float(self.get_parameter('lidar_quantization_m').value)
        self.compass_noise_stddev = float(self.get_parameter('compass_noise_stddev_deg').value)
        self.noise = random.Random(int(self.get_parameter('sensor_noise_seed').value))
        self.camera_width = int(self.get_parameter('camera_width').value)
        self.camera_height = int(self.get_parameter('camera_height').value)
        self.camera_fov_deg = float(self.get_parameter('camera_fov_deg').value)
        self.twist_timeout = float(self.get_parameter('twist_timeout_sec').value)
        self.wheel_separation = float(self.get_parameter('wheel_separation_m').value)

        pose = self.floorplan.start_pose
        self.x, self.y = float(pose['x']), float(pose['y'])
        self.theta = float(pose['theta_deg']) % 360.0
        self.head_deg = self.head_center_deg
        self.neck_deg = NEUTRAL_ANGLES['neck']
        self.linear, self.angular = 0.0, 0.0
        self.last_twist_at = time.monotonic()
        self.last_tick_at = time.monotonic()

        self.range_pub = self.create_publisher(Range, 'lidar/range', 10)
        self.heading_pub = self.create_publisher(Float32, 'compass/heading_deg', 10)
        self.image_pub = self.create_publisher(Image, 'camera/image_raw', 5)
        self.world_state_pub = self.create_publisher(String, 'simulation/world_state', 5)
        self.battery_pub = self.create_publisher(BatteryState, 'battery_state', 1)

        self.create_subscription(Twist, 'simulation/applied_twist', self._applied_twist, 5)
        self.create_subscription(JointState, 'servo/joint_states', self._joint_states, 10)

        self.create_timer(1.0 / 50.0, self._tick)
        self.create_timer(1.0 / 12.0, self._publish_camera)
        self.create_timer(1.0, self._publish_battery)

    def _applied_twist(self, msg):
        if math.isfinite(msg.linear.x) and math.isfinite(msg.angular.z):
            self.linear, self.angular = msg.linear.x, msg.angular.z
            self.last_twist_at = time.monotonic()

    def _joint_states(self, msg):
        for name, position in zip(msg.name, msg.position):
            if not math.isfinite(position):
                continue
            if name in self.rig['joints']:
                self.joints_deg[name] = math.degrees(position)
            if name == 'headLeftRight':
                self.head_deg = math.degrees(position)
            elif name == 'neck':
                self.neck_deg = max(0.0, min(120.0, math.degrees(position)))

    def _look_bearing(self, offset_deg):
        return (self.theta + self.head_sign * (self.head_deg - self.head_center_deg)
                + offset_deg) % 360.0

    def _eye_pose(self, sensor):
        angles = dict(self.joints_deg, neck=self.neck_deg, headLeftRight=self.head_deg)
        pose = sensor_pose(angles, sensor, self.x, self.y, self.theta, self.rig)
        offset = self.lidar_yaw_offset_deg if sensor == 'lidar' else self.camera_yaw_offset_deg
        if offset:
            c, s = math.cos(math.radians(offset)), math.sin(math.radians(offset))
            for key in ('direction', 'up'):
                x, y, z = pose[key]
                pose[key] = [c*x+s*y, -s*x+c*y, z]
            pose['bearing_deg'] = (pose['bearing_deg'] + offset) % 360
        return pose

    def _noisy_lidar(self, distance):
        measured = distance + self.noise.gauss(0.0, self.lidar_noise_stddev)
        if self.lidar_quantization > 0:
            measured = round(measured / self.lidar_quantization) * self.lidar_quantization
        return max(self.lidar_min_range, min(self.lidar_max_range, measured))

    def _noisy_heading(self):
        return (self.theta + self.noise.gauss(0.0, self.compass_noise_stddev)) % 360.0

    def _tick(self):
        now = time.monotonic()
        dt = now - self.last_tick_at
        self.last_tick_at = now
        if now - self.last_twist_at > self.twist_timeout:
            self.linear, self.angular = 0.0, 0.0
        # Bound both time and travel per step, including after a stalled frame.
        steps = max(1, math.ceil(dt / .02),
                    math.ceil(abs(self.linear) * dt / min(.02, self.body_radius / 2)))
        step_dt = dt / steps
        for _ in range(steps):
            rad = math.radians(self.theta)
            candidate_x = self.x + self.linear * step_dt * math.sin(rad)
            candidate_y = self.y + self.linear * step_dt * math.cos(rad)
            if self.floorplan.clearance(candidate_x, candidate_y) > self.body_radius:
                self.x, self.y = candidate_x, candidate_y
            # ROS yaw is counterclockwise; compass bearings increase clockwise.
            self.theta = (self.theta - math.degrees(self.angular * step_dt)) % 360.0

        stamp = self.get_clock().now().to_msg()
        lidar_pose = self._eye_pose('lidar')
        lidar_bearing = lidar_pose['bearing_deg']
        hit = self.sensor_world.raycast(lidar_pose, self.lidar_max_range, self.lidar_min_range)
        range_msg = Range(radiation_type=Range.INFRARED, field_of_view=0.05,
                           min_range=self.lidar_min_range, max_range=self.lidar_max_range,
                           range=self._noisy_lidar(hit.distance_m))
        range_msg.header.stamp = stamp
        range_msg.header.frame_id = 'lidar_link'
        self.range_pub.publish(range_msg)
        compass_heading = self._noisy_heading()
        self.heading_pub.publish(Float32(data=compass_heading))

        self.world_state_pub.publish(String(data=json.dumps({
            'applied_twist': {'linear_mps': self.linear, 'angular_rps': self.angular},
            'wheel_separation_m': self.wheel_separation,
            'pose': {'x': self.x, 'y': self.y, 'theta_deg': self.theta},
            'robot_dimensions_m': {'width': self.robot_width, 'height': self.robot_height,
                                   'collision_radius': self.body_radius},
            'lidar_pose': lidar_pose,
            'camera_pose': self._camera_state(),
            'head_deg': self.head_deg, 'neck_deg': self.neck_deg,
            'mapping_pose_valid': mapping_sensor_pose_valid(self.joints_deg),
            'compass_heading_deg': compass_heading,
            'lidar_bearing_deg': lidar_bearing,
            'lidar_range_m': range_msg.range, 'lidar_ground_truth_m': hit.distance_m,
            'lidar_hit': hit.hit,
            'floorplan_file': self.floorplan_file})))

    def _publish_camera(self):
        pose = self._camera_state()
        camera_x, camera_y = pose['x'], pose['y']
        bearing, pitch = pose['bearing_deg'], pose['pitch_deg']
        if self.get_parameter('camera_renderer').value == 'panda3d':
            if self.scene_camera is None:
                from .scene_camera import SceneCamera
                self.scene_camera = SceneCamera(self.floorplan, self.camera_width,
                                                self.camera_height, self.camera_fov_deg)
            frame = self.scene_camera.render(camera_x, camera_y, pose['z'], bearing, pitch,
                                             direction=pose['direction'], up=pose['up'])
        else:
            frame = renderer.render(self.floorplan, camera_x, camera_y, bearing,
                                    width=self.camera_width, height=self.camera_height,
                                    fov_deg=self.camera_fov_deg, pitch_deg=pitch)
        msg = Image()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'camera_link'
        msg.height, msg.width = frame.shape[0], frame.shape[1]
        msg.encoding = 'bgr8'
        msg.step = msg.width * 3
        msg.data = frame.tobytes()
        self.image_pub.publish(msg)

    def _camera_state(self):
        return self._eye_pose('camera')

    def destroy_node(self):
        if self.scene_camera is not None:
            self.scene_camera.close()
            self.scene_camera = None
        return super().destroy_node()

    def _publish_battery(self):
        self.battery_pub.publish(BatteryState(voltage=12.4, current=0.8,
                                               percentage=0.8, present=True))


def main(args=None):
    rclpy.init(args=args)
    node = WorldNode()
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
