from types import SimpleNamespace

from kufibot_perception.tracking import select_largest


def detection(width, height, name):
    return SimpleNamespace(width=width, height=height, id=name)


def test_largest_face_wins_even_when_hand_is_larger():
    face = detection(0.2, 0.3, 'face')
    hand = detection(0.9, 0.9, 'hand')
    assert select_largest([face], [hand]) is face


def test_largest_hand_is_fallback():
    small = detection(0.1, 0.1, 'small')
    large = detection(0.2, 0.3, 'large')
    assert select_largest([], [small, large]) is large


def test_no_detection_returns_none():
    assert select_largest([], []) is None
