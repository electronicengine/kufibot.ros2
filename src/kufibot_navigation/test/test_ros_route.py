"""Real ROS FollowRoute server/client, with a deterministic raycast room."""
import json
import math
import time
import uuid

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import Point
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

from kufibot_interfaces.action import FollowRoute
from kufibot_interfaces.msg import DriveCommand
from kufibot_navigation.node import NavigationNode
from kufibot_simulation.floorplan import FloorPlan, Wall


def test_ros_whole_route_feedback_clean_image_and_cancellation():
    rclpy.init(args=['--ros-args', '-r', '__ns:=/route_' + uuid.uuid4().hex])
    node, client = NavigationNode(), Node('route_test_client')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(client)
    action = ActionClient(client, FollowRoute, 'navigation/follow_route')
    nav = node.nav
    nav.c.calibrated = nav.c.circular_footprint = True
    nav.c.angular_speed_min_rad_s, nav.c.angular_speed_max_rad_s = .25, 1.
    nav.c.settle_sec = .05
    walls = [Wall(a, b, 'room') for a, b in [((-2, -2), (2, -2)), ((2, -2), (2, 2)),
                                              ((2, 2), (-2, 2)), ((-2, 2), (-2, -2))]]
    room = FloorPlan([], walls, [])
    pose = [0., 0., 0.]
    head = [90.]
    applied = [0., 0.]
    previous = [time.monotonic()]
    states, moving_states = [], []
    client.create_subscription(String, 'navigation/state', lambda m: states.append(json.loads(m.data)), 10)

    def drive(msg):
        applied[:] = [msg.twist.linear.x, msg.twist.angular.z]
        if any(applied):
            moving_states.append(nav.route_plan and dict(nav.route_plan))
    client.create_subscription(DriveCommand, 'drive/command', drive, 10)

    def ray(bearing):
        hit = room.raycast(pose[0], pose[1], bearing)
        nav.update_simulation(dict(pose=dict(x=pose[0], y=pose[1], theta_deg=pose[2]),
            lidar_pose=dict(x=pose[0], y=pose[1], bearing_deg=bearing),
            lidar_range_m=hit.distance_m, lidar_hit=hit.hit))
        return hit.distance_m

    def feed():
        now = time.monotonic()
        dt, previous[0] = min(.025, now-previous[0]), now
        pose[2] = (pose[2]-math.degrees(applied[1]*dt)) % 360
        pose[0] += math.sin(math.radians(pose[2]))*applied[0]*dt
        pose[1] += math.cos(math.radians(pose[2]))*applied[0]*dt
        nav.set_authority(dict(epoch='e', enabled=True, owner=True, provider='verasist', mode='ai'))
        nav.set_session('s', True)
        nav.applied_mode, nav.applied_at = 'ai', now
        target, neck = nav.head_target or (90., 60.)
        head[0] += max(-1.8, min(1.8, target-head[0]))
        nav.sensor('heading', pose[2])
        nav.sensor('joints', dict(headLeftRight=head[0], neck=neck))
        nav.sensor('range', ray(pose[2]+90-head[0]))
        nav.sensor('image', Image(height=8, width=8, step=24, encoding='bgr8',
                                data=np.full((8, 8, 3), 80, dtype=np.uint8).tobytes()))
    client.create_timer(.01, feed)

    def until(predicate, seconds=30):
        end = time.monotonic()+seconds
        while not predicate() and time.monotonic() < end:
            executor.spin_once(timeout_sec=.005)
        assert predicate(), 'ROS route did not settle'

    def result(future, seconds=30):
        until(future.done, seconds)
        return future.result()

    def goal(rid, points):
        return FollowRoute.Goal(session_id='s', task_id=nav.task_id, request_id=rid,
            map_id=nav.metric_map.id, map_revision=nav.metric_map.revision,
            waypoints=[Point(x=x, y=y) for x, y in points])

    try:
        until(lambda: nav.enabled and action.server_is_ready())
        # A previously measured full sweep; only lidar hits are admitted, never room walls.
        for angle in range(360):
            ray(angle)
        nav.task('start', 's', request_id='start')
        feedback = []
        handle = result(action.send_goal_async(goal('route', [(0., .35), (.35, .35)]),
                                              feedback_callback=lambda m: feedback.append(m.feedback)))
        assert handle.accepted
        completed = json.loads(result(handle.get_result_async(), 45).result.result_json)
        assert completed['status'] == 'ok', completed
        assert completed['route_plan']['completed_count'] == 2
        assert moving_states and all(s and len(s['waypoints']) == 2 for s in moving_states)
        assert states and feedback
        assert any(f.active_index == 1 for f in feedback)
        assert math.dist(pose[:2], [.35, .35]) < .15
        observation = nav.observations[completed['observation_id']]
        jpeg = node.encode(observation['frames'][0][1], observation)
        decoded = cv2.imdecode(np.frombuffer(bytes(jpeg.data), np.uint8), cv2.IMREAD_COLOR)
        assert np.max(np.abs(decoded.astype(int)-80)) <= 1  # no overlay at all
        assert observation['map']['boundary_paths']
        handle = result(action.send_goal_async(goal('cancel', [(.35, .8), (.35, 1.)])))
        assert handle.accepted
        result(handle.cancel_goal_async())
        cancelled = json.loads(result(handle.get_result_async()).result.result_json)
        assert cancelled['status'] == 'cancelled'
        assert nav.drive == (0., 0.)
        assert nav.route_plan['status'] == 'cancelled'
    finally:
        action.destroy()
        executor.remove_node(node)
        executor.remove_node(client)
        node.destroy_node()
        client.destroy_node()
        executor.shutdown()
        rclpy.shutdown()
