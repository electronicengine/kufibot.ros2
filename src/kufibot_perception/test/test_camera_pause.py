"""Camera capture stays available despite legacy speech pause requests."""

import time
from unittest.mock import Mock

from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool

from kufibot_interaction.camera_pause import CameraPauseLease, attach_camera_pause
from kufibot_perception.camera_node import UsbCameraNode


def camera():
    node = UsbCameraNode.__new__(UsbCameraNode)
    node.camera_pause = CameraPauseLease()
    node.capture = Mock()
    node.active_device = '/dev/test-camera'
    node.last_connect_attempt = time.monotonic()
    node.reconnect_interval = 2.0
    node._connect_camera = Mock()
    return node


def test_speech_pause_does_not_release_usb_or_interrupt_camera_analysis():
    node = camera()
    capture = node.capture
    capture.read.return_value = (True, object())
    node._publish = Mock()
    node.camera_pause.update('voice', True)
    node._capture()
    node._capture()
    capture.release.assert_not_called()
    assert capture.read.call_count == 2
    assert node._publish.call_count == 2
    node._connect_camera.assert_not_called()
    assert node.capture is capture
    node.camera_pause.update('voice', False)
    node._capture()
    assert capture.read.call_count == 3


def test_disconnected_camera_can_reconnect_during_speech():
    node = camera()
    node.capture = None
    node.last_connect_attempt = 0.0
    node.camera_pause.update('voice', True)
    node._capture()
    node._connect_camera.assert_called_once()
    node.camera_pause.update('voice', False)
    node._capture()
    assert node._connect_camera.call_count == 2


def test_each_source_must_be_idle_and_dead_publishers_expire():
    now = [10.0]
    lease = CameraPauseLease(clock=lambda: now[0])
    lease.update('voice', True)
    lease.update('remote', True)
    lease.update('voice', False)
    assert lease.paused
    now[0] += 1.5
    lease.update('remote', True)
    now[0] += 1.0
    assert lease.paused  # Heartbeats renew an active performance.
    now[0] += 1.0
    assert not lease.paused


def test_ros_idle_from_one_source_does_not_resume_another_active_source():
    context = Context()
    context.init(domain_id=189)
    transport = Node('camera_pause_test', context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(transport)
    lease = attach_camera_pause(transport)
    voice = transport.create_publisher(Bool, 'voice_session/camera_paused', 1)
    remote = transport.create_publisher(Bool, 'remote/camera_paused', 1)

    def deliver(publisher, value, predicate):
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            publisher.publish(Bool(data=value))
            executor.spin_once(timeout_sec=.05)
            if predicate():
                return
        assert predicate()

    try:
        deliver(voice, True, lambda: 'voice_session/camera_paused' in lease.deadlines)
        deliver(remote, True, lambda: 'remote/camera_paused' in lease.deadlines)
        deliver(voice, False, lambda: 'voice_session/camera_paused' not in lease.deadlines)
        assert lease.paused
        deliver(remote, False, lambda: not lease.paused)
    finally:
        executor.shutdown()
        transport.destroy_node()
        context.shutdown()
