"""Real ROS control acknowledgements; provider/audio doubles, no hardware drivers."""
import asyncio
from copy import deepcopy
import json
import time
import uuid
from unittest.mock import AsyncMock

import pytest
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from kufibot_interaction.activation_controller import ActivationController
from kufibot_interaction.voice_activation import DEFAULT_ACTIVATION
from kufibot_interaction.servo_arbiter import ServoArbiter
from kufibot_remote.node import RemoteController


@pytest.mark.parametrize('provider', ['local', 'verasist'])
def test_ros_wake_handoff_and_normal_end(provider):
    async def scenario():
        rclpy.init(args=['--ros-args', '-r', '__ns:=/activation_' + uuid.uuid4().hex[:8]])
        harness = Node('voice_harness')
        remote, arbiter = RemoteController(), ServoArbiter()
        executor = SingleThreadedExecutor()
        for node in (harness, remote, arbiter):
            executor.add_node(node)
        harness.session_active = harness.starting = False
        harness.mic_playback_echo_tail = 0
        harness.ai_settings = {'provider':provider, 'activation':deepcopy(DEFAULT_ACTIVATION)}
        harness._publish_state = lambda *args: None
        harness._apply_pending_voice_settings = lambda: None
        events = []
        async def start():
            events.append(provider)
            harness.session_active = True
        async def stop():
            harness.session_active = False
        harness._start_session, harness._stop_session = start, stop
        c = ActivationController(harness)
        publisher = harness.create_publisher(String, 'voice_session/control_request', 10)
        harness._request_activation_mode = lambda id, mode: publisher.publish(String(data=json.dumps({
            'id':id, 'mode':mode, 'expected_mode':c.mode, 'epoch':c.control_epoch})))
        harness.create_subscription(String, 'voice_session/control_state',
            lambda msg: asyncio.create_task(c.control_state(json.loads(msg.data))), 10)
        harness.create_subscription(String, 'voice_session/control_ack',
            lambda msg: c.acknowledge(json.loads(msg.data)), 10)
        harness.create_subscription(String, 'remote/applied_mode',
            lambda msg: asyncio.create_task(c.mode_changed(msg.data)), 10)
        harness.create_subscription(String, 'voice_session/finish',
            lambda msg: asyncio.create_task(c.finish('manual')), 10)
        c.prepare = AsyncMock()
        c.config = deepcopy(DEFAULT_ACTIVATION)
        async def cue(name):
            events.append(name)
        c.cue = cue
        awake = asyncio.Event()
        async def worker(operation):
            assert operation == 'listen'
            c.phase = 'wake_listening'
            await awake.wait()
            awake.clear()
            return True
        c.run_worker = worker
        async def spin():
            previous = time.monotonic()
            while True:
                executor.spin_once(timeout_sec=0)
                now = time.monotonic()
                if now - previous >= .02:
                    remote.tick(now-previous)
                    previous = now
                await asyncio.sleep(.001)
        async def until(predicate):
            async with asyncio.timeout(5):
                while not predicate():
                    await asyncio.sleep(.01)
        job = asyncio.create_task(spin())
        try:
            await until(lambda: c.phase == 'wake_listening')
            assert remote.control.mode == 'remote' and not harness.session_active
            owner = object()
            remote.control.command(owner, {'type':'claim'})
            remote.control.command(owner, {'type':'mode','mode':'remote'})
            await until(lambda: c.mode == 'remote' and c.phase == 'wake_listening'
                        and c.control_epoch == remote.control.mode_epoch)
            remote.control.command(owner, {'type':'input','drive_y':-1})
            awake.set()
            await until(lambda: harness.session_active)
            assert remote.control.mode == arbiter._mode == 'ai'
            assert not any(remote.control.axes.values())
            assert events == ['greeting', provider]
            remote.control.command(owner, {'type':'stopVoice'})
            await until(lambda: remote.control.mode == 'remote' and c.phase == 'wake_listening')
            assert events == ['greeting', provider, 'farewell']
            assert not harness.session_active
        finally:
            await c.shutdown()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            executor.shutdown()
            for node in (harness, remote, arbiter):
                node.destroy_node()
            rclpy.shutdown()
    asyncio.run(scenario())
