"""Real DDS subscription/lease expiry, with no hardware nodes or motion topics."""
import json
import time

import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy
from std_msgs.msg import String

from kufibot_interaction.local_voice_runtime import attach_phase_lease


def test_phase_heartbeat_late_subscriber_expiry_and_idle():
    context = Context()
    rclpy.init(context=context, domain_id=187)
    publisher_node = Node('voice_phase_test_publisher', context=context)
    subscriber_node = Node('voice_phase_test_consumer', context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(publisher_node)
    executor.add_node(subscriber_node)
    changed = []
    try:
        publisher = publisher_node.create_publisher(String, 'local_ai/phase',
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        publisher.publish(String(data=json.dumps({'phase': 'thinking'})))
        lease = attach_phase_lease(subscriber_node, changed.append)
        def until(predicate, timeout=4):
            end = time.monotonic() + timeout
            while not predicate() and time.monotonic() < end:
                executor.spin_once(timeout_sec=.05)
            assert predicate()
        until(lambda: changed == [True])
        until(lambda: changed == [True, False])  # Dead publisher lease expires.
        publisher.publish(String(data=json.dumps({'phase': 'thinking'})))
        until(lambda: changed[-1] is True)
        publisher.publish(String(data=json.dumps({'phase': 'idle'})))
        until(lambda: changed[-1] is False)
        assert not lease.busy
    finally:
        executor.shutdown()
        subscriber_node.destroy_node()
        publisher_node.destroy_node()
        rclpy.shutdown(context=context)
