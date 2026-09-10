"""Raycasting and clearance correctness for the floor-plan model."""
import math
from pathlib import Path

import pytest

from kufibot_simulation.floorplan import Door, FloorPlan, Obstacle, Room, Wall

DEFAULT = str(Path(__file__).parents[1] / 'kufibot_simulation' / 'floorplans'
              / 'apartment_default.json')


def box(room_id='room', label='Room', color=(100, 100, 100), rect=(0, 0, 4, 4)):
    return Room(id=room_id, label=label, color=color, rect=rect)


def test_raycast_hits_straight_wall_and_reports_owning_room():
    walls = [Wall(a=(0, 4), b=(4, 4), room_id='room')]
    plan = FloorPlan([box()], walls, [])
    hit = plan.raycast(2, 0, bearing_deg=0, max_range=8.0)
    assert hit.valid and hit.room_id == 'room'
    assert math.isclose(hit.distance_m, 4.0, abs_tol=1e-6)
    assert hit.hit == pytest.approx((2.0, 4.0))


def test_raycast_reports_max_range_when_nothing_in_view():
    plan = FloorPlan([box()], [], [])
    hit = plan.raycast(0, 0, bearing_deg=90, max_range=5.0)
    assert not hit.valid
    assert hit.distance_m == 5.0


def test_raycast_clamps_to_min_range_when_wall_is_too_close():
    walls = [Wall(a=(0, 0.05), b=(4, 0.05), room_id='room')]
    plan = FloorPlan([box()], walls, [])
    hit = plan.raycast(2, 0, bearing_deg=0, max_range=8.0, min_range=0.2)
    assert hit.valid and hit.distance_m == 0.2


def test_ray_passes_through_a_door_gap_to_the_room_beyond():
    # Two rooms share a wall at y=2 with a 1m door gap; the far wall is at y=6.
    walls = [
        Wall(a=(0, 2), b=(1.5, 2), room_id='near'),
        Wall(a=(2.5, 2), b=(4, 2), room_id='near'),
        Wall(a=(0, 6), b=(4, 6), room_id='far'),
    ]
    doors = [Door(id='d', label='Door', a=(1.5, 2), b=(2.5, 2), connects=('near', 'far'))]
    plan = FloorPlan([box('near'), box('far', rect=(0, 2, 4, 6))], walls, doors)
    hit = plan.raycast(2, 0, bearing_deg=0, max_range=8.0)
    assert hit.valid and hit.room_id == 'far'
    assert math.isclose(hit.distance_m, 6.0, abs_tol=1e-6)


def test_doors_on_ray_reports_door_before_the_far_wall():
    walls = [Wall(a=(0, 6), b=(4, 6), room_id='far')]
    doors = [Door(id='d', label='Door', a=(1.5, 2), b=(2.5, 2), connects=('near', 'far'))]
    plan = FloorPlan([box()], walls, doors)
    hits = plan.doors_on_ray(2, 0, bearing_deg=0, max_range=8.0)
    assert len(hits) == 1
    distance, door = hits[0]
    assert door.id == 'd' and math.isclose(distance, 2.0, abs_tol=1e-6)


def test_clearance_ignores_doors_and_measures_nearest_wall():
    walls = [Wall(a=(0, 0), b=(0, 4), room_id='room')]
    doors = [Door(id='d', label='Door', a=(-1, 2), b=(1, 2), connects=('a', 'b'))]
    plan = FloorPlan([box()], walls, doors)
    assert math.isclose(plan.clearance(0.3, 2.0), 0.3, abs_tol=1e-6)


def test_point_in_room_uses_room_rects():
    plan = FloorPlan([box('a', rect=(0, 0, 2, 2)), box('b', rect=(2, 0, 4, 2))], [], [])
    assert plan.point_in_room(1, 1) == 'a'
    assert plan.point_in_room(3, 1) == 'b'
    assert plan.point_in_room(10, 10) is None


def test_default_apartment_floorplan_loads_and_start_pose_has_clearance():
    plan = FloorPlan.load(DEFAULT)
    assert set(plan.rooms) == {'living_room', 'corridor', 'kitchen'}
    assert len(plan.doors) == 2
    pose = plan.start_pose
    assert plan.clearance(pose['x'], pose['y']) > 0.2


def test_default_apartment_has_a_clear_path_from_living_room_to_kitchen():
    plan = FloorPlan.load(DEFAULT)
    # Straight down the corridor toward the kitchen door: nothing should
    # block a ray cast from the corridor into the kitchen through the gap.
    hit = plan.raycast(-3.5, 2.0, bearing_deg=0, max_range=8.0)
    assert hit.valid and hit.room_id == 'kitchen'


def test_raycast_hits_furniture_before_the_far_wall_and_reports_its_id():
    walls = [Wall(a=(0, 6), b=(4, 6), room_id='room')]
    obstacles = [Obstacle(id='sofa', label='Sofa', color=(1, 2, 3), rect=(1, 2, 3, 3))]
    plan = FloorPlan([box()], walls, [], obstacles)
    hit = plan.raycast(2, 0, bearing_deg=0, max_range=8.0)
    assert hit.valid and hit.obstacle_id == 'sofa'
    assert math.isclose(hit.distance_m, 2.0, abs_tol=1e-6)


def test_clearance_treats_furniture_as_a_solid_obstacle():
    obstacles = [Obstacle(id='table', label='Table', color=(1, 2, 3), rect=(1, 1, 2, 2))]
    plan = FloorPlan([box()], [], [], obstacles)
    assert math.isclose(plan.clearance(0.7, 1.5), 0.3, abs_tol=1e-6)


def test_default_apartment_furniture_loads_and_does_not_block_the_kitchen_door():
    plan = FloorPlan.load(DEFAULT)
    assert {'sofa', 'table', 'chair', 'cabinet', 'desk', 'tv', 'coffee_table'} == set(o.id for o in plan.obstacles)
    hit = plan.raycast(-3.5, 2.0, bearing_deg=0, max_range=8.0)
    assert hit.obstacle_id is None  # the table doesn't sit on this ray
