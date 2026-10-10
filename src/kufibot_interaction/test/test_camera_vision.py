import asyncio
import threading
import time
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

from kufibot_interaction.voice_agent_node import CAMERA_QOS, VoiceAgentNode


def snapshot(width=800, height=400, encoding='bgr8'):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :, 1] = 180
    return {
        'received_at': time.monotonic(), 'width': width, 'height': height,
        'step': width * 3, 'encoding': encoding, 'data': frame.tobytes(),
    }


def test_camera_frame_is_resized_and_encoded_as_jpeg():
    jpeg = VoiceAgentNode._encode_camera_jpeg(snapshot(), 640, 80)
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (320, 640)


@pytest.mark.parametrize('reliability', [ReliabilityPolicy.BEST_EFFORT,
                                        ReliabilityPolicy.RELIABLE])
def test_ros_camera_frame_reaches_sdk(reliability):
    """USB sensor-data and simulation publishers both reach the vision tool."""
    import uuid
    context = Context()
    context.init()
    transport = Node('camera_vision_test', context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(transport)
    node = camera_node()
    node.latest_camera_image = None
    node.last_camera_sample_at = 0.0
    node.camera_sample_interval = 0.2
    node.camera_send_cooldown = 0.0
    node.last_camera_send_at = 0.0
    node.ice_timeout = 3.0
    topic = '/camera_vision_test_' + uuid.uuid4().hex
    try:
        transport.create_subscription(Image, topic, node._camera_image, CAMERA_QOS)
        publisher = transport.create_publisher(
            Image, topic, QoSProfile(depth=1, reliability=reliability))
        frame = snapshot(320, 240)
        message = Image(width=frame['width'], height=frame['height'],
                        step=frame['step'], encoding=frame['encoding'],
                        data=frame['data'])
        deadline = time.monotonic() + 5.0
        while node.latest_camera_image is None and time.monotonic() < deadline:
            publisher.publish(message)
            executor.spin_once(timeout_sec=0.05)
        assert node.latest_camera_image is not None
        node.session.send_image.return_value = {'status': 'success'}
        result = asyncio.run(node._send_camera_image(node.session, 'look'))
        assert result['status'] == 'success'
        jpeg = node.session.send_image.await_args.kwargs['image_bytes']
        assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (240, 320, 3)
    finally:
        executor.shutdown()
        transport.destroy_node()
        context.shutdown()


def test_camera_image_is_sent_through_new_sdk_api():
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.camera_lock = threading.Lock()
    node.latest_camera_image = snapshot(320, 240)
    node.camera_max_age = 1.0
    node.camera_jpeg_max_width = 640
    node.camera_jpeg_quality = 80
    node.camera_send_cooldown = 2.0
    node.last_camera_send_at = 0.0
    node.ice_timeout = 3.0
    node.get_logger = lambda: type(
        'Logger', (), {'info': lambda self, message: None})()
    session = type('Session', (), {})()
    session.send_image = AsyncMock(return_value={'status': 'success'})
    result = asyncio.run(node._send_camera_image(session, 'What is visible?'))
    assert result['status'] == 'success'
    kwargs = session.send_image.await_args.kwargs
    assert kwargs['mime_type'] == 'image/jpeg'
    assert kwargs['prompt'] == 'What is visible?'
    assert kwargs['trigger_response'] is True
    assert kwargs['image_bytes'].startswith(b'\xff\xd8')


def test_stale_camera_frame_is_rejected_without_upload():
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.camera_lock = threading.Lock()
    node.latest_camera_image = snapshot()
    node.latest_camera_image['received_at'] -= 5.0
    node.camera_max_age = 1.0
    node.camera_send_cooldown = 0.0
    node.last_camera_send_at = 0.0
    session = type('Session', (), {'send_image': AsyncMock()})()
    result = asyncio.run(node._send_camera_image(session, 'look'))
    assert result['status'] == 'error'
    assert result['error'] == 'camera frame is stale'
    session.send_image.assert_not_awaited()


def camera_node():
    from unittest.mock import Mock
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.camera_attach_to_every_user_turn = True
    node.camera_turn_task = None
    node.camera_turn_id = None
    node.camera_send_tasks = set()
    node.camera_lock = threading.Lock()
    node.latest_camera_image = snapshot()
    node.camera_max_age = 1.0
    node.camera_jpeg_max_width = 640
    node.camera_jpeg_quality = 80
    node._publish_ai_settings = Mock()
    node.get_logger = Mock()
    node.session = Mock(send_image=AsyncMock(), complete_camera_turn=AsyncMock())
    return node


def test_camera_event_sends_one_snapshot_per_turn():
    async def run():
        node = camera_node()
        event = {'type': 'rtf-user-turn-started', 'payload': {'turn_id': 'one'}}
        node._voice_event(node.session, event)
        node._voice_event(node.session, event)
        await node.camera_turn_task
        node.session.send_image.assert_awaited_once()
        kwargs = node.session.send_image.await_args.kwargs
        assert kwargs['turn_id'] == 'one'
        assert kwargs['trigger_response'] is False
        node.session.complete_camera_turn.assert_awaited_once_with('one')
    asyncio.run(run())


def test_disabled_camera_does_not_upload():
    async def run():
        node = camera_node()
        node.camera_attach_to_every_user_turn = False
        node._voice_event(node.session, {'type': 'rtf-user-turn-started', 'payload': {'turn_id': 'one'}})
        node.session.send_image.assert_not_awaited()
    asyncio.run(run())


def test_consecutive_turns_each_attach_the_latest_frame():
    async def run():
        node = camera_node()
        for turn_id, width in [('one', 320), ('two', 160)]:
            node.latest_camera_image = snapshot(width, 120)
            node._voice_event(node.session, {
                'type': 'rtf-user-turn-started', 'payload': {'turn_id': turn_id}})
            await node.camera_turn_task
        calls = node.session.send_image.await_args_list
        assert [call.kwargs['turn_id'] for call in calls] == ['one', 'two']
        assert all(call.kwargs['trigger_response'] is False for call in calls)
        assert [cv2.imdecode(np.frombuffer(call.kwargs['image_bytes'], np.uint8),
                            cv2.IMREAD_COLOR).shape[1] for call in calls] == [320, 160]
        assert node.session.complete_camera_turn.await_count == 2
    asyncio.run(run())


def test_missing_camera_completes_turn_without_image():
    async def run():
        node = camera_node()
        node.latest_camera_image = None
        node._voice_event(node.session, {'type': 'rtf-user-turn-started', 'payload': {'turn_id': 'one'}})
        await node.camera_turn_task
        node.session.send_image.assert_not_awaited()
        node.session.complete_camera_turn.assert_awaited_once_with('one')
        assert 'eklenemedi' in node.camera_status
    asyncio.run(run())


def test_new_turn_cancels_previous_pending_upload():
    async def run():
        node = camera_node()
        blocker = asyncio.Event()
        node.session.send_image.side_effect = lambda **kwargs: None
        async def upload(**kwargs):
            if kwargs['turn_id'] == 'one':
                await blocker.wait()
        node.session.send_image.side_effect = upload
        node._voice_event(node.session, {'type': 'rtf-user-turn-started', 'payload': {'turn_id': 'one'}})
        old = node.camera_turn_task
        await asyncio.sleep(0.02)
        node._voice_event(node.session, {'type': 'rtf-user-turn-started', 'payload': {'turn_id': 'two'}})
        await asyncio.gather(old, node.camera_turn_task)
        node.session.complete_camera_turn.assert_awaited_once_with('two')
    asyncio.run(run())
