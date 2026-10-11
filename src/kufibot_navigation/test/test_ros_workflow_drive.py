"""Workflow demands through the actual ROS arbiter in an isolated namespace."""
import json
import time
import uuid

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from kufibot_interfaces.msg import DriveCommand
from kufibot_navigation.node import NavigationNode


def test_ros_workflow_forward_backward_and_owner_loss():
    rclpy.init(args=['--ros-args', '-r', '__ns:=/workflow_drive_test_'+uuid.uuid4().hex])
    arbiter = NavigationNode()
    client = Node('workflow_client')
    executor = SingleThreadedExecutor()
    executor.add_node(arbiter)
    executor.add_node(client)
    publishers = {name: client.create_publisher(String, name, 10) for name in (
        'navigation/authority', 'workflow/session', 'workflow/drive_request', 'drive/limits', 'remote/applied_mode')}
    outputs, results = [], []
    client.create_subscription(DriveCommand, 'drive/command', outputs.append, 10)
    client.create_subscription(String, 'workflow/drive_result', lambda msg: results.append(json.loads(msg.data)), 10)
    owner = [True]
    def send(name, value):
        publishers[name].publish(String(data=json.dumps(value)))
    def feed():
        send('navigation/authority', {'epoch': 'epoch', 'mode': 'ai', 'owner': owner[0], 'enabled': False,
                                     'provider': 'local', 'workflow_enabled': True, 'workflow_session_id': 'local'})
        send('workflow/session', {'session_id': 'local', 'connected': True})
        send('drive/limits', {'min_speed_mps': .25, 'max_speed_mps': .5})
        publishers['remote/applied_mode'].publish(String(data='ai'))
    client.create_timer(.05, feed)
    def until(predicate, seconds=4):
        deadline = time.monotonic()+seconds
        while not predicate() and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
        assert predicate()
    def demand(identifier, direction, duration=.2):
        send('workflow/drive_request', {'request_id': identifier, 'session_id': 'local', 'direction': direction,
                                       'speed_mps': .25, 'duration_sec': duration, 'operation': 'move'})
    try:
        until(lambda: arbiter.workflow_drive.allowed('local', arbiter.nav.applied_mode, arbiter.nav.applied_at)
                      and arbiter.workflow_drive.limits_ready)
        demand('forward', 'forward')
        until(lambda: any(r['request_id'] == 'forward' for r in results))
        assert any(m.profile == 'manual' and m.twist.linear.x == .25 for m in outputs)
        assert next(r for r in results if r['request_id'] == 'forward')['status'] == 'ok'
        demand('backward', 'backward')
        until(lambda: any(r['request_id'] == 'backward' for r in results))
        assert any(m.profile == 'manual' and m.twist.linear.x == -.25 for m in outputs)
        demand('cancel', 'forward', 5)
        until(lambda: arbiter.workflow_drive.pending is not None)
        owner[0] = False
        until(lambda: any(r['request_id'] == 'cancel' for r in results))
        assert next(r for r in results if r['request_id'] == 'cancel')['status'] == 'cancelled'
        until(lambda: outputs[-1].twist.linear.x == 0)
    finally:
        arbiter.nav.metric_map.close()
        executor.shutdown()
        client.destroy_node()
        arbiter.destroy_node()
        rclpy.shutdown()
