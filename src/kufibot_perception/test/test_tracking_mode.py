import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from sensor_msgs.msg import Image
from kufibot_perception.mediapipe_node import MediaPipeNode
from kufibot_perception.tracking_controller import PolarHeadTracker


def test_inference_and_commands_only_in_confirmed_ai_mode():
    node = MediaPipeNode.__new__(MediaPipeNode)
    node.applied_mode = 'remote'
    node.mode_time = time.monotonic()
    node.local_compute_active = False
    node.latest_target = None
    node.last_target_time = node.last_inference = 0.0
    node.inference_interval = .1
    node.timeout = .25
    node.debug = False
    node.face = SimpleNamespace(process=Mock(return_value=SimpleNamespace(detections=[])))
    node.hands = SimpleNamespace(process=Mock(return_value=SimpleNamespace(multi_hand_landmarks=[])))
    node.faces_pub = node.hands_pub = node.target_pub = SimpleNamespace(publish=Mock())
    node.command_pub = SimpleNamespace(publish=Mock())
    node.tracker = PolarHeadTracker()
    node.neutral_step = 1.0
    node.get_clock = lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: Image().header.stamp))
    msg = Image(height=4, width=4, step=12, encoding='bgr8',
                data=np.zeros((4, 4, 3), np.uint8).tobytes())
    node._image(msg)
    node._control_tick()
    node.face.process.assert_not_called()
    node.hands.process.assert_not_called()
    node.command_pub.publish.assert_not_called()

    node._mode(SimpleNamespace(data='ai'))
    node._image(msg)
    node._control_tick()
    node.face.process.assert_called_once()
    node.hands.process.assert_called_once()
    node.command_pub.publish.assert_called_once()

    node.latest_target = (.8, .5)
    node._mode(SimpleNamespace(data='remote'))
    assert node.latest_target is None
    node._image(msg)
    node._control_tick()
    node.face.process.assert_called_once()
    node.command_pub.publish.assert_called_once()
    node._mode(SimpleNamespace(data='ai'))
    node.mode_time -= 2
    assert not node._tracking_enabled()
