from kufibot_simulation.plan_editor import EditorState
from kufibot_simulation.floorplan import FloorPlan
import pytest


def test_catalog_move_rotate_save_reload(tmp_path):
    state = EditorState(tmp_path / 'home.json')
    state.catalog_index = 2
    state.place_furniture((2, 3))
    state.rotate_furniture()
    state.move_selected((4, 5))
    state.save()
    item = FloorPlan.load(str(state.path)).obstacles[0]
    assert item.kind == 'sofa'
    assert item.yaw_deg == 90
    assert item.rect == pytest.approx((3.55, 4.1, 4.45, 5.9))
    state.undo()
    assert state.obstacles[0].rect == pytest.approx((1.55, 2.1, 2.45, 3.9))
