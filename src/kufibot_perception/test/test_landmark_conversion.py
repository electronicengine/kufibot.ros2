from types import SimpleNamespace

import pytest

from kufibot_perception.mediapipe_node import MediaPipeNode


def test_face_2d_keypoint_gets_zero_depth():
    point = SimpleNamespace(x=0.25, y=0.75)
    landmark = MediaPipeNode._landmark(3, point)
    assert landmark.index == 3
    assert landmark.x == 0.25
    assert landmark.y == 0.75
    assert landmark.z == 0.0
    assert landmark.visibility == 1.0


def test_hand_3d_landmark_preserves_depth_and_visibility():
    point = SimpleNamespace(x=0.1, y=0.2, z=-0.3, visibility=0.8)
    landmark = MediaPipeNode._landmark(4, point)
    assert landmark.z == pytest.approx(-0.3)
    assert landmark.visibility == pytest.approx(0.8)
