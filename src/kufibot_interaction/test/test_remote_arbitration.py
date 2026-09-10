import json
import math
import threading
import time

import pytest
from types import SimpleNamespace

from kufibot_interfaces.msg import JointCommand
from std_msgs.msg import String

from kufibot_interaction.servo_arbiter import ServoArbiter


def arbiter():
    node = ServoArbiter.__new__(ServoArbiter)
    node._lock = threading.Lock()
    node._mode = 'ai'
    node._remote = {}
    node._remote_seen = 0.0
    node._agent = {'neck': 80.0}
    node._tracking = {'neck': 70.0}
    node._agent_until = None
    node._current = {'neck': math.radians(60), 'leftArm': math.radians(160)}
    node._motion = {}
    node._motion_until = 0.0
    node.get_logger = lambda: SimpleNamespace(warning=lambda _: None)
    return node


def test_remote_takes_priority_and_clears_ai_commands():
    node = arbiter()
    node._remote_callback(String(data=json.dumps({'mode': 'remote', 'targets': {'neck': 65}})))
    assert node._mode == 'remote'
    assert node._remote['neck'] == 65
    assert node._remote['leftArm'] == 160
    assert node._agent == node._tracking == {}
    ai = JointCommand(names=['neck'], angles_deg=[90.0])
    node._agent_callback(ai)
    node._tracking_callback(ai)
    assert node._agent == node._tracking == {}
    node._remote_callback(String(data=json.dumps({'mode': 'ai', 'targets': {}})))
    node._tracking_callback(ai)
    assert node._tracking == {'neck': 90}


def test_invalid_remote_packet_cannot_change_mode():
    node = arbiter()
    for packet in ['null', '[]', '{', '{"mode":"remote","targets":{"neck":"nan"}}']:
        node._remote_callback(String(data=packet))
    assert node._mode == 'ai'


def test_bridge_loss_holds_remote_position_without_enabling_ai():
    from rclpy.time import Time

    node = arbiter()
    node._remote_callback(String(data=json.dumps({'mode': 'remote', 'targets': {'neck': 65}})))
    node._remote_seen = 0.0
    targets, modes = [], []
    node.publisher = SimpleNamespace(publish=targets.append)
    node.mode_pub = SimpleNamespace(publish=modes.append)
    node.get_clock = lambda: SimpleNamespace(now=lambda: Time())
    node._publish()
    assert node._mode == 'remote'
    assert modes[-1].data == 'unavailable'
    assert math.isclose(targets[-1].position[targets[-1].name.index('neck')], math.radians(65))


def test_moving_posture_overrides_remote_arms_only_while_held():
    from rclpy.time import Time

    node = arbiter()
    node._mode = 'remote'
    node._remote = {'leftArm': 170.0, 'rightArm': 15.0, 'neck': 65.0}
    node._remote_seen = time.monotonic()
    node._motion_callback(JointCommand(names=['rightArm', 'leftArm'],
                                       angles_deg=[40.0, 140.0], hold_sec=.25))
    targets, modes = [], []
    node.publisher = SimpleNamespace(publish=targets.append)
    node.mode_pub = SimpleNamespace(publish=modes.append)
    node.get_clock = lambda: SimpleNamespace(now=lambda: Time())
    node._publish()
    values = dict(zip(targets[-1].name, (math.degrees(x) for x in targets[-1].position)))
    assert values == {'leftArm': 140.0, 'rightArm': 40.0, 'neck': 65.0}
    node._motion_callback(JointCommand(cancel_agent=True))
    node._publish()
    values = dict(zip(targets[-1].name, (math.degrees(x) for x in targets[-1].position)))
    assert values['leftArm'] == 170.0
    assert values['rightArm'] == pytest.approx(15.0)
    assert values['neck'] == 65.0
