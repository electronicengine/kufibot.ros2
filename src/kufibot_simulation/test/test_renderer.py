"""Schematic camera renderer: pure-function checks, no rclpy or real images."""
import numpy as np

from kufibot_simulation.floorplan import Door, FloorPlan, Obstacle, Room, Wall
from kufibot_simulation import renderer


def small_plan():
    rooms = [Room(id='near', label='Near', color=(200, 0, 0), rect=(-2, -2, 2, 2)),
              Room(id='far', label='Far', color=(0, 200, 0), rect=(-2, 4, 2, 8))]
    walls = [Wall(a=(-2, 4), b=(-0.5, 4), room_id='near'),
             Wall(a=(0.5, 4), b=(2, 4), room_id='near'),
             Wall(a=(-2, 8), b=(2, 8), room_id='far')]
    doors = [Door(id='d', label='Kapi', a=(-0.5, 4), b=(0.5, 4), connects=('near', 'far'))]
    return FloorPlan(rooms, walls, doors)


def test_render_output_shape_and_dtype():
    frame = renderer.render(small_plan(), 0, 0, bearing_deg=0, width=320, height=240)
    assert frame.shape == (240, 320, 3)
    assert frame.dtype == np.uint8


def test_upward_pitch_moves_the_horizon_downward_from_level():
    plan = FloorPlan([], [], [])
    level = renderer.render(plan, 0, 0, bearing_deg=0, width=32, height=120)
    looking_up = renderer.render(plan, 0, 0, bearing_deg=0, width=32, height=120,
                                 pitch_deg=45)
    # The sky/floor split and the distant projection move together.
    assert not np.array_equal(level, looking_up)
    assert np.array_equal(looking_up[-1, 0], renderer.FLOOR_COLOR)


def plan_with_only_wall(room_id, wall_y, color):
    rooms = [Room(id=room_id, label=room_id, color=color, rect=(-2, 0, 2, wall_y))]
    walls = [Wall(a=(-2, wall_y), b=(2, wall_y), room_id=room_id)]
    return FloorPlan(rooms, walls, [])


def test_render_paints_the_forward_wall_with_its_room_color():
    plan = plan_with_only_wall('near', wall_y=4, color=(200, 0, 0))
    frame = renderer.render(plan, 0, 0, bearing_deg=0, width=320, height=240, fov_deg=10)
    center_col = frame[:, 160, :]
    assert center_col.max(axis=0)[0] > center_col.max(axis=0)[1]  # red-dominant wall


def test_render_wall_color_reflects_the_owning_room():
    plan = plan_with_only_wall('far', wall_y=3, color=(0, 200, 0))
    frame = renderer.render(plan, 0, 0, bearing_deg=0, width=320, height=240, fov_deg=10)
    center_col = frame[:, 160, :]
    assert center_col.max(axis=0)[1] > center_col.max(axis=0)[0]  # green-dominant wall


def test_render_draws_a_door_marker_when_the_door_is_in_view():
    plain = renderer.render(small_plan(), 0, -0.01, bearing_deg=0, width=320, height=240, fov_deg=60)
    door_color = np.array(renderer.DOOR_COLOR)
    matches = np.all(np.abs(plain.astype(int) - door_color) < 10, axis=-1)
    assert matches.any()


def test_render_paints_furniture_with_its_own_color_when_nearer_than_the_wall():
    rooms = [Room(id='room', label='Room', color=(0, 0, 200), rect=(-2, 0, 2, 6))]
    walls = [Wall(a=(-2, 6), b=(2, 6), room_id='room')]
    obstacles = [Obstacle(id='sofa', label='Sofa', color=(0, 200, 0), rect=(-1, 2, 1, 3))]
    plan = FloorPlan(rooms, walls, [], obstacles)
    frame = renderer.render(plan, 0, 0, bearing_deg=0, width=320, height=240, fov_deg=10)
    center_col = frame[:, 160, :]
    assert center_col.max(axis=0)[1] > center_col.max(axis=0)[2]  # green furniture, not the blue room wall
