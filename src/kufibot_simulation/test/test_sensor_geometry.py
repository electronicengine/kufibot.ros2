import math

import pytest
from panda3d.core import NodePath, Point3, Vec3

from kufibot_interaction.joint_limits import NEUTRAL_ANGLES
from kufibot_simulation.scene import Robot
from kufibot_simulation.sensor_geometry import SensorWorld, sensor_pose
from kufibot_simulation.floorplan import FloorPlan, Room, Wall, Obstacle


@pytest.mark.parametrize('neck,head', [(0, 90), (10, 90), (120, 40), (60, 180)])
def test_optical_pose_matches_articulated_mesh(neck, head):
    root = NodePath('world')
    robot = Robot(root)
    robot.root.setPos(1, 2, 0)
    robot.root.setH(-35)
    angles = dict(NEUTRAL_ANGLES, neck=neck, headLeftRight=head)
    robot.apply_joints(angles)
    for sensor, mount in robot.rig['sensors'].items():
        eye = robot.joints[mount['parent']]
        x, y, z = mount['position_m']
        point = root.getRelativePoint(eye, Point3(x, -z, y))
        forward = root.getRelativeVector(eye, Vec3(0, 1, 0))
        up = root.getRelativeVector(eye, Vec3(0, 0, 1))
        pose = sensor_pose(angles, sensor, 1, 2, 35)
        assert [pose[k] for k in ('x', 'y', 'z')] == pytest.approx(tuple(point), abs=1e-6)
        assert pose['direction'] == pytest.approx(tuple(forward), abs=1e-6)
        assert pose['up'] == pytest.approx(tuple(up), abs=1e-6)


def test_lidar_uses_slant_range_and_can_look_over_a_wall():
    world = SensorWorld(FloorPlan([], [Wall((-5, 2), (5, 2), 'room')], []))
    level = sensor_pose(dict(neck=0, headLeftRight=90, eyeLeft=0), 'lidar')
    hit = world.raycast(level)
    assert hit.valid
    assert hit.distance_m == pytest.approx(1.99-level['y'], abs=1e-5)
    raised = sensor_pose(dict(neck=60, headLeftRight=90, eyeLeft=0), 'lidar')
    hit = world.raycast(raised)
    assert hit.distance_m == pytest.approx((1.99-raised['y'])/math.cos(math.radians(21)), abs=1e-5)
    distant = SensorWorld(FloorPlan([], [Wall((-5, 5), (5, 5), 'room')], []))
    assert not distant.raycast(sensor_pose(dict(neck=120), 'lidar')).valid


def test_lidar_intersects_floor_and_passes_above_low_furniture():
    plan = FloorPlan([Room('room', 'Room', (100, 100, 100), (-5, -5, 5, 5))], [], [],
                     [Obstacle('coffee', 'coffee', (100, 100, 100), (-1, 1, 1, 2))])
    world = SensorWorld(plan)
    downward = dict(x=0, y=0, z=.5, direction=[0, 0, -1])
    assert world.raycast(downward).distance_m == pytest.approx(.5)
    assert world.raycast(dict(x=0, y=0, z=.4, direction=[0, 1, 0])).valid
    assert not world.raycast(dict(x=0, y=0, z=.6, direction=[0, 1, 0])).valid
