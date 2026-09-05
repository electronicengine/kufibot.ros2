"""Pure selection helpers for perception tests."""


def select_largest(faces, hands):
    """Select largest face, falling back to largest hand."""
    candidates = faces if faces else hands
    return max(candidates, default=None, key=lambda item: item.width * item.height)
