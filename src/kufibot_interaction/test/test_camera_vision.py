import asyncio
import threading
import time
from unittest.mock import AsyncMock

import cv2
import numpy as np

from kufibot_interaction.voice_agent_node import VoiceAgentNode


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
