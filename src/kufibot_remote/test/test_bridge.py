import base64
import time
from types import SimpleNamespace

import cv2
import numpy as np
from sensor_msgs.msg import Image

from kufibot_remote.control import Control
from kufibot_remote.node import RemoteController


def bridge():
    node = RemoteController.__new__(RemoteController)
    node.control = Control()
    node.current = {}
    node.sensors = {}
    node.frame_time = node.last_encode = node.mode_time = 0
    node.jpeg = None
    node.applied_mode = None
    node.calibration = {'active': False, 'samples': 0, 'target': 500, 'message': ''}
    node.ai_trigger_uuid = ''
    node.local_compute_active = False
    node.drive_pub = SimpleNamespace(get_subscription_count=lambda: 0)
    node.get_logger = lambda: SimpleNamespace(warning=lambda _: None)
    return node


def test_padded_rgb_frame_is_resized_and_encoded():
    node = bridge()
    pixels = np.zeros((480, 800 * 3 + 8), np.uint8)
    pixels[:, :800 * 3:3] = 255
    msg = Image(height=480, width=800, step=2408, encoding='rgb8', data=pixels.tobytes())
    node._image(msg)
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(node.jpeg), np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (384, 640, 3)
    assert decoded[0, 0, 2] > 240
    assert decoded[0, 0, 0] < 10


def test_invalid_frame_is_ignored():
    node = bridge()
    node._image(Image(height=480, width=640, step=1920, encoding='bgr8', data=b'bad'))
    assert node.jpeg is None


def test_local_llm_compute_stops_camera_encoding_and_clears_old_frame():
    node = bridge()
    node.jpeg = 'old-frame'
    node.frame_time = time.monotonic()
    node._local_compute(SimpleNamespace(data=True))
    assert node.jpeg is None
    assert not node.status()['camera']
    pixels = np.zeros((4, 12), np.uint8)
    node._image(Image(height=4, width=4, step=12, encoding='bgr8', data=pixels.tobytes()))
    assert node.jpeg is None
    node._local_compute(SimpleNamespace(data=False))
    node._image(Image(height=4, width=4, step=12, encoding='bgr8', data=pixels.tobytes()))
    assert node.jpeg is not None


def test_stale_sensors_and_missing_arbiter_are_explicit():
    node = bridge()
    node._sensor('voltage', 12.2)
    node._sensor('heading', float('nan'))
    node.sensors['distance'] = (1.2, time.monotonic() - 4)
    state = node.status()
    assert state['sensors'] == {'voltage': 12.2, 'heading': None, 'distance': None}
    assert state['appliedMode'] is None
    assert not state['camera']
    assert not state['driveAvailable']


def test_missing_arbiter_prevents_drive_output():
    node = bridge()
    messages = []
    node.drive_pub = SimpleNamespace(publish=messages.append)
    node.remote_pub = SimpleNamespace(publish=lambda _: None)
    owner = object()
    node.control.command(owner, {'type': 'claim'})
    node.control.command(owner, {'type': 'input', 'drive_y': -1})
    node.tick(.05)
    assert messages[-1].linear.x == 0
    node.applied_mode, node.mode_time = 'remote', time.monotonic()
    node.control.command(owner, {'type': 'input', 'drive_y': -1})
    node.tick(.05)
    assert messages[-1].linear.x == .25
