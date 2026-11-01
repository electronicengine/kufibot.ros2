"""Tools-mode expressions reach the real arbiter in an isolated ROS namespace."""
import asyncio
import math
import uuid

import rclpy
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import JointState

from kufibot_interaction.joint_limits import NEUTRAL_ANGLES
from kufibot_interaction.servo_arbiter import ServoArbiter
from kufibot_remote.node import RemoteController


def test_tools_expression_reaches_arbiter_without_motor_motion(monkeypatch, tmp_path):
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE', str(tmp_path / 'mimics.json'))
    namespace = '/expression_test_' + uuid.uuid4().hex[:8]

    async def run():
        rclpy.init(args=['--ros-args', '-r', '__ns:=' + namespace])
        remote, arbiter = RemoteController(), ServoArbiter()
        executor = SingleThreadedExecutor()
        executor.add_node(remote)
        executor.add_node(arbiter)
        frames, drives = [], []
        remote.create_subscription(JointState, 'servo/joint_targets', frames.append, 10)
        from geometry_msgs.msg import Twist
        remote.create_subscription(Twist, 'drive/manual_cmd', drives.append, 10)
        feedback = remote.create_publisher(JointState, 'servo/joint_states', 10)
        async def tick():
            feedback.publish(JointState(name=list(NEUTRAL_ANGLES),
                position=[math.radians(v) for v in NEUTRAL_ANGLES.values()]))
            remote.tick(.02)
            for _ in range(15):
                executor.spin_once(timeout_sec=0)
            await asyncio.sleep(.02)
        try:
            remote.control.command('owner', {'type': 'claim'})
            remote.control.command('owner', {'type': 'mode', 'mode': 'tools'})
            async with asyncio.timeout(5):
                while remote.applied_mode != 'tools':
                    await tick()
            motion = remote.control.mimic_store.save(dict(id='expression_test', name='Test',
                description='', revision=0, duration_ms=400, interpolation='linear', keyframes=[
                    dict(time_ms=0, joints=dict(NEUTRAL_ANGLES)),
                    dict(time_ms=400, joints={**NEUTRAL_ANGLES, 'rightArm': 55.})]))
            remote.control.play_motion('owner', motion, mode='tools')
            async with asyncio.timeout(3):
                while remote.control.active_mimic is not None:
                    await tick()
            for _ in range(8):
                await tick()
            assert any('rightArm' in frame.name and math.degrees(
                frame.position[frame.name.index('rightArm')]) > 35 for frame in frames)
            assert drives and all(msg.linear.x == 0 and msg.angular.z == 0 for msg in drives)
        finally:
            executor.shutdown()
            remote.destroy_node()
            arbiter.destroy_node()
            rclpy.shutdown()
    asyncio.run(run())
