import math

import pytest
from panda3d.core import NodePath
from kufibot_interaction.joint_limits import JOINT_LIMITS
from kufibot_simulation.scene import Robot, segment_fraction


def test_camera_segment_collision():
    assert segment_fraction((0,-3,0), (0,3,0), (1,1,1), 0) == pytest.approx(1/3)
    assert segment_fraction((2,-3,0), (2,3,0), (1,1,1), 0) == 1
    assert segment_fraction((0,0,0), (0,3,0), (1,1,1), 0) == 0


def test_joint_limits_and_hierarchy():
    from panda3d.core import Quat, Vec3
    from kufibot_interaction.robot_model import joint_rotation
    robot = Robot(NodePath('scene'))
    for endpoint in (0, 1):
        values = {name: bounds[endpoint] for name, bounds in JOINT_LIMITS.items()}
        robot.apply_joints(values)
        for name, spec in robot.rig['joints'].items():
            x, y, z = spec['axis']
            expected = Quat()
            expected.setFromAxisAngleRad(joint_rotation(spec, values[name]), Vec3(x,-z,y))
            assert abs(robot.joints[name].getQuat().dot(expected)) == pytest.approx(1, abs=1e-6)
    assert robot.joints['eyeLeft'].getParent() == robot.joints['headLeftRight']
    assert robot.joints['headLeftRight'].getParent() == robot.joints['neck']
    robot.apply_joints({'headLeftRight': 1000})
    assert robot.joints['headLeftRight'].getH() == pytest.approx(90)
    low, high = robot.root.getTightBounds()
    assert all(math.isfinite(v) for v in (*low, *high))
    assert robot.rig['source_name'] == 'Wall-E_Assembly_NotForPrinting.stl'
    assert robot.rig['triangles'] < 160000


def test_wheels_forward_reverse_and_turn():
    robot = Robot(NodePath('scene'))
    assert len(robot.wheels) == 8
    robot.animate_wheels(.1, 0, .2, .1)
    for wheel, spec in zip(robot.wheels, robot.rig['wheels'].values()):
        assert wheel.getP() == pytest.approx((-math.degrees(.01/spec['radius_m'])) % 360)
    robot.animate_wheels(-.1, 0, .2, .1)
    assert all(min(abs(w.getP()), abs(w.getP()-360)) < .001 for w in robot.wheels)
    robot.animate_wheels(0, 1, .2, .1)
    assert robot.wheels[0].getP() != pytest.approx(robot.wheels[4].getP())


def test_physical_servo_reference_poses():
    from panda3d.core import Vec3

    robot = Robot(NodePath('scene'))
    robot.apply_joints(dict(neck=0, headLeftRight=90, eyeLeft=0, eyeRight=170,
                           leftArm=170, rightArm=10))
    # Forward is +Y and up is +Z in Panda. The head looks level at neck=0.
    forward = robot.joints['neck'].getQuat().xform(Vec3(0, 1, 0))
    assert forward.z == pytest.approx(0, abs=1e-6)
    for name in ('leftArm', 'rightArm'):
        forward = robot.joints[name].getQuat().xform(Vec3(0, 1, 0))
        assert math.degrees(math.atan2(forward.z, forward.y)) == pytest.approx(-37.5)
    robot.apply_joints(dict(neck=120, eyeLeft=40, eyeRight=140))
    assert robot.joints['neck'].getQuat().xform(Vec3(0, 1, 0)).z > 0
    # Each eye's outer edge moves downward, with opposite servo directions.
    assert robot.joints['eyeLeft'].getQuat().xform(Vec3(-1, 0, 0)).z < 0
    assert robot.joints['eyeRight'].getQuat().xform(Vec3(1, 0, 0)).z < 0


def test_model_startup_matches_hardware_defaults():
    from kufibot_interaction.joint_limits import NEUTRAL_ANGLES

    expected = dict(rightArm=15, leftArm=170, neck=10, headLeftRight=90,
                    eyeRight=170, eyeLeft=0)
    robot = Robot(NodePath('scene'))
    assert NEUTRAL_ANGLES == expected
    assert {name: spec['neutral_deg'] for name, spec in robot.rig['joints'].items()} == expected
    assert robot.joints['neck'].getP() == pytest.approx(3.5)
