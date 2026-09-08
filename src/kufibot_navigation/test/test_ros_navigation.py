"""Hardware-free ROS actions and arbitration in an isolated test namespace."""
import json
import time
import uuid

import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import Image
from kufibot_interfaces.action import NavigateStep
from kufibot_interfaces.msg import DriveCommand
from kufibot_interfaces.srv import NavigationTask, GetObservation
from kufibot_navigation.node import NavigationNode


def test_ros_action_result_observation_cancellation_and_manual_arbitration():
    namespace = '/navigation_test_' + uuid.uuid4().hex
    rclpy.init(args=['--ros-args', '-r', '__ns:=' + namespace])
    node = NavigationNode()
    client = Node('test_client')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(client)
    steps = ActionClient(client, NavigateStep, 'navigation/step')
    tasks = client.create_client(NavigationTask, 'navigation/task')
    observations = client.create_client(GetObservation, 'navigation/observation')
    outputs = []
    client.create_subscription(DriveCommand, 'drive/command', outputs.append, 10)
    nav = node.nav
    mode = ['ai']

    def feed():
        nav.set_authority(dict(epoch=mode[0], enabled=mode[0] == 'ai', owner=True,
                               provider='verasist', mode=mode[0]))
        nav.set_session('session', True)
        nav.applied_mode, nav.applied_at = mode[0], time.monotonic()
        nav.sensor('heading', 0.)
        nav.sensor('range', 2.)
        head, neck = nav.head_target or (90., 60.)
        nav.sensor('joints', dict(headLeftRight=head, neck=neck))
        image = Image(height=4, width=4, step=12, encoding='bgr8',
                      data=np.zeros((4, 4, 3), np.uint8).tobytes())
        image.header.stamp = client.get_clock().now().to_msg()
        nav.sensor('image', image)
        nav.manual, nav.manual_at = (.4, 0.), time.monotonic()

    client.create_timer(.02, feed)

    def until(predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
        assert predicate(), 'ROS operation did not complete'

    def result(future):
        until(future.done)
        return future.result()

    try:
        until(lambda: tasks.service_is_ready() and steps.server_is_ready() and nav.enabled)
        started = json.loads(result(tasks.call_async(NavigationTask.Request(
            session_id='session', operation='start', request_id='start', label='test'))).result_json)
        task_id = started['task_id']
        goal = result(steps.send_goal_async(NavigateStep.Goal(session_id='session',
            task_id=task_id, request_id='scan', operation='observe', sweep_deg=0.)))
        assert goal.accepted
        scanned = json.loads(result(goal.get_result_async()).result.result_json)
        assert scanned['status'] == 'ok'
        obs = result(observations.call_async(GetObservation.Request(session_id='session',
            task_id=task_id, observation_id=scanned['observation_id'])))
        assert json.loads(obs.result_json)['status'] == 'ok'
        assert len(obs.images) == 1 and bytes(obs.images[0].data).startswith(b'\xff\xd8')
        assert outputs and all(m.twist.linear.x == 0 and m.twist.angular.z == 0 for m in outputs)
        goal = result(steps.send_goal_async(NavigateStep.Goal(session_id='session',
            task_id=task_id, request_id='cancel-scan', operation='observe', sweep_deg=180.)))
        assert goal.accepted
        result(goal.cancel_goal_async())
        assert json.loads(result(goal.get_result_async()).result.result_json)['status'] == 'cancelled'
        mode[0] = 'remote'
        until(lambda: any(m.profile == 'manual' and m.twist.linear.x == .4 for m in outputs))
        # No voice-session dependency for manual driving; loss of authority stops it.
        mode[0] = 'unavailable'
        until(lambda: outputs[-1].twist.linear.x == 0)
    finally:
        steps.destroy()
        executor.remove_node(node)
        executor.remove_node(client)
        node.destroy_node()
        client.destroy_node()
        executor.shutdown()
        rclpy.shutdown()
