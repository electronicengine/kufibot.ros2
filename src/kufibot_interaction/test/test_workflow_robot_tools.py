import asyncio
import base64
import copy
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kufibot_interaction.joint_limits import NEUTRAL_ANGLES
from kufibot_interaction.workflow_robot_tools import TimedCache, validate_tool_arguments
from kufibot_interaction.workflows import render_speech


def motion():
    return {'id': 'node-motion', 'name': 'Kol hareketi', 'description': '', 'revision': 0,
            'duration_ms': 100, 'interpolation': 'linear', 'keyframes': [
                {'time_ms': 0, 'joints': dict(NEUTRAL_ANGLES)},
                {'time_ms': 100, 'joints': {**NEUTRAL_ANGLES, 'rightArm': 55}}]}


def voice_node():
    from kufibot_interaction.voice_agent_node import VoiceAgentNode
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.expression_lock = threading.RLock()
    node.expression_generation = 0
    node.expression_worker = Mock()
    node.expression_queue = []
    node.active_motion = None
    node.speech_motion_active = False
    node.expression_reset_pending = True
    node.workflow_motion = None
    node.local_process = None
    node._record_workflow_event = Mock()
    node._publish_gesture = Mock()
    return node


def test_workflow_motion_uses_snapshot_and_ignores_talking():
    node = voice_node()
    original = motion()
    asyncio.run(node._local_workflow_tool('play_motion', {'motion': original}))
    original['keyframes'][-1]['joints']['rightArm'] = 10
    assert node.workflow_motion[0]['keyframes'][-1]['joints']['rightArm'] == 55
    node._start_speech_motion()
    assert not node.speech_motion_active and node.workflow_motion
    node._motion_tick()
    assert node._publish_gesture.called
    saved, _ = node.workflow_motion
    node.workflow_motion = (saved, time.monotonic()-1)
    node._motion_tick()
    assert node.workflow_motion is None
    assert node._record_workflow_event.call_args.args[0]['type'] == 'motion_complete'


def test_workflow_motion_can_be_cancelled_or_replaced():
    node = voice_node()
    node._workflow_start_motion(motion())
    node._workflow_start_motion(motion())
    events = [call.args[0]['type'] for call in node._record_workflow_event.call_args_list]
    assert events == ['motion_start', 'motion_cancelled', 'motion_start']
    node._workflow_cancel_motion()
    assert node.workflow_motion is None


def test_invalid_motion_is_rejected_before_running():
    value = motion()
    value['keyframes'][0]['joints']['rightArm'] = 999
    with pytest.raises(ValueError, match='rightArm'):
        validate_tool_arguments('play_motion', {'motion': value})


@pytest.mark.parametrize('kwargs', [{'direction': 'left'}, {'speed_mps': -1}, {'duration_sec': 6}, {'duration_sec': True}])
def test_invalid_drive_arguments(kwargs):
    with pytest.raises(ValueError):
        validate_tool_arguments('move_base', {'direction': 'forward', 'speed_mps': .25, 'duration_sec': 1, **kwargs})


def test_camera_preserves_unavailable_perception_and_rejects_stale_frame():
    node = voice_node()
    node.camera_lock = threading.Lock()
    node.camera_max_age = node.max_age = 1
    node.camera_jpeg_max_width, node.camera_jpeg_quality = 640, 80
    node.cache = TimedCache()
    node.cache.set('faces', [])
    node.latest_camera_image = {'received_at': time.monotonic()}
    node._encode_camera_jpeg = Mock(return_value=b'jpeg')
    result = asyncio.run(node._local_workflow_tool('read_camera', {}))
    assert base64.b64decode(result['image']['base64']) == b'jpeg'
    assert result['perception']['faces']['status'] == 'ok'
    assert result['perception']['faces']['value'] == []
    assert result['perception']['objects']['status'] == 'unavailable'
    node.latest_camera_image['received_at'] -= 2
    assert asyncio.run(node._local_workflow_tool('read_camera', {}))['status'] == 'error'
    assert node._encode_camera_jpeg.call_count == 1


def test_stale_sensor_value_is_not_spoken_as_current():
    with pytest.raises(ValueError, match='güncel değil'):
        render_speech('Mesafe {{last_result.range_m.value}}', {'last_result': {'range_m': {'status': 'stale', 'value': 2}}})


def test_workflow_drive_waits_for_authority_and_stops_on_cancellation():
    async def scenario():
        node = voice_node()
        node.loop = asyncio.get_running_loop()
        node.workflow_drive_pending = {}
        node.workflow_session_id = 'local'
        node.workflow_authority = {'owner': True, 'mode': 'ai', 'workflow_enabled': True, 'workflow_session_id': 'local'}
        node.workflow_authority_at = time.monotonic()
        node._publish_ai_settings = Mock()
        node.workflow_drive_pub = Mock()
        published = []
        import json
        def publish(message):
            published.append(json.loads(message.data))
        node.workflow_drive_pub.publish.side_effect = publish
        task = asyncio.create_task(node._workflow_base_tool('move_base', {'direction': 'forward', 'speed_mps': .25, 'duration_sec': 1}))
        while not published:
            await asyncio.sleep(.001)
        assert published[0]['session_id'] == 'local' and published[0]['operation'] == 'move'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert published[-1]['operation'] == 'stop'
        assert not node.workflow_drive_pending
    asyncio.run(scenario())
