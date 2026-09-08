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
    robot = Robot(NodePath('scene'))
    for endpoint in (0, 1):
        values = {name: bounds[endpoint] for name, bounds in JOINT_LIMITS.items()}
        robot.apply_joints(values)
        assert robot.joints['headLeftRight'].getH() == pytest.approx(values['headLeftRight']-90)
        assert robot.joints['leftArm'].getP() == pytest.approx(180-values['leftArm'])
        assert robot.joints['rightArm'].getP() == pytest.approx(values['rightArm']-10)
        assert robot.joints['neck'].getP() == pytest.approx(
            values['neck'] * robot.neck_up_degrees_per_servo_degree)
        assert robot.joints['eyeLeft'].getR() == pytest.approx(values['eyeLeft']-30)
        assert robot.joints['eyeRight'].getR() == pytest.approx(values['eyeRight']-150)
    assert robot.joints['eyeLeft'].getParent() == robot.joints['headLeftRight']
    assert robot.joints['headLeftRight'].getParent() == robot.joints['neck']
    robot.apply_joints({'headLeftRight': 1000})
    assert robot.joints['headLeftRight'].getH() == 90
    assert robot.root.getScale().z == pytest.approx(robot.model_scale)
    assert robot.height_m == pytest.approx(.28)


def test_wheels_forward_reverse_and_turn():
    robot = Robot(NodePath('scene'))
    robot.animate_wheels(.1, 0, .2, .1)
    expected = (-math.degrees(.1)) % 360
    assert all(w.getP() == pytest.approx(expected) for w in robot.wheels)
    robot.animate_wheels(-.1, 0, .2, .1)
    assert all(min(abs(w.getP()), abs(w.getP()-360)) < .001 for w in robot.wheels)
    robot.animate_wheels(0, 1, .2, .1)
    assert robot.wheels[0].getP() != pytest.approx(robot.wheels[1].getP())
