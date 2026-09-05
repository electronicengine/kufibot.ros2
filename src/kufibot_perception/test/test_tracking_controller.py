import pytest

from kufibot_perception.tracking_controller import PolarHeadTracker


def test_target_area_does_not_move_head():
    tracker = PolarHeadTracker()
    assert tracker.update(0.55, 0.5, 640, 480) == (90.0, 60.0, False)


def test_target_on_right_turns_head_right():
    tracker = PolarHeadTracker()
    head, neck, moving = tracker.update(0.9, 0.5, 640, 480)
    assert moving and head < 90.0
    assert neck == pytest.approx(60.0)


def test_target_above_moves_neck_up():
    tracker = PolarHeadTracker()
    head, neck, moving = tracker.update(0.5, 0.1, 640, 480)
    assert moving and neck > 60.0
    assert head == pytest.approx(90.0)


def test_diagonal_target_moves_both_axes():
    tracker = PolarHeadTracker()
    head, neck, _ = tracker.update(0.9, 0.1, 640, 480)
    assert head < 90.0 and neck > 60.0


def test_step_is_clamped_to_configured_maximum():
    tracker = PolarHeadTracker()
    head, _, _ = tracker.update(1.0, 0.5, 640, 480)
    assert head == pytest.approx(87.5)


def test_tracker_brakes_when_approaching_target_area():
    tracker = PolarHeadTracker()
    far_step = 90.0 - tracker.update(1.0, 0.5, 640, 480)[0]
    tracker.head = 90.0
    near_step = 90.0 - tracker.update(0.64, 0.5, 640, 480)[0]
    assert 0.0 < near_step < far_step


def test_hysteresis_prevents_boundary_chatter():
    tracker = PolarHeadTracker()
    assert tracker.update(0.62, 0.5, 640, 480)[2] is False
    assert tracker.update(0.64, 0.5, 640, 480)[2] is True
    # Once moving it stops at 70 px, then ignores noise until 85 px.
    assert tracker.update(0.60, 0.5, 640, 480)[2] is False
    assert tracker.update(0.62, 0.5, 640, 480)[2] is False


def test_joint_limits_are_never_exceeded():
    tracker = PolarHeadTracker()
    for _ in range(100):
        tracker.update(1.0, 1.0, 640, 480)
    assert tracker.head == 0.0 and tracker.neck == 0.0


def test_resolution_scales_dead_zone():
    tracker = PolarHeadTracker()
    assert tracker.update(0.55, 0.5, 1280, 960)[2] is False


def test_target_loss_returns_to_neutral_incrementally():
    tracker = PolarHeadTracker()
    tracker.head, tracker.neck = 70.0, 80.0
    assert tracker.move_to_neutral(1.0) == (71.0, 79.0)
