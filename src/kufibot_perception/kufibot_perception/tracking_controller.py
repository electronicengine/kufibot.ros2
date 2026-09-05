"""Incremental head tracker modelled after landmark_tracker_service.cpp."""

import math


class PolarHeadTracker:
    """Convert a pixel target to C++-style 1..4 degree joint increments."""

    def __init__(self, *, head_neutral=90.0, head_minimum=0.0,
                 head_maximum=180.0, neck_neutral=60.0,
                 neck_minimum=0.0, neck_maximum=120.0,
                 error_threshold_px=70.0, release_threshold_px=85.0,
                 max_error_px=300.0, minimum_step_deg=0.15,
                 maximum_step_deg=2.5):
        self.head_neutral = float(head_neutral)
        self.head_minimum = float(head_minimum)
        self.head_maximum = float(head_maximum)
        self.neck_neutral = float(neck_neutral)
        self.neck_minimum = float(neck_minimum)
        self.neck_maximum = float(neck_maximum)
        self.error_threshold_px = float(error_threshold_px)
        self.release_threshold_px = float(release_threshold_px)
        self.max_error_px = float(max_error_px)
        self.minimum_step_deg = float(minimum_step_deg)
        self.maximum_step_deg = float(maximum_step_deg)
        self.head = self.head_neutral
        self.neck = self.neck_neutral
        self.in_target_area = True

    def _strength(self, magnitude):
        span = max(1.0, self.max_error_px - self.error_threshold_px)
        progress = max(0.0, min(
            1.0, (magnitude - self.error_threshold_px) / span))
        # Smoothstep has zero slope at the target-area edge. This brakes the
        # mechanism before it crosses the centre and starts correcting back.
        progress = progress * progress * (3.0 - 2.0 * progress)
        scaled = self.minimum_step_deg + progress * (
            self.maximum_step_deg - self.minimum_step_deg)
        return max(self.minimum_step_deg, min(self.maximum_step_deg, scaled))

    def update(self, target_x, target_y, width, height):
        """Advance both targets once; target coordinates are normalized."""
        error_x = (float(target_x) - 0.5) * float(width)
        error_y = (float(target_y) - 0.5) * float(height)
        resolution_scale = min(float(width) / 640.0, float(height) / 480.0)
        resolution_scale = max(resolution_scale, 0.01)
        threshold = self.error_threshold_px * resolution_scale
        release_threshold = self.release_threshold_px * resolution_scale
        maximum = self.max_error_px * resolution_scale
        magnitude = math.hypot(error_x, error_y)
        if self.in_target_area:
            self.in_target_area = magnitude < release_threshold
        elif magnitude <= threshold:
            self.in_target_area = True
        if self.in_target_area:
            return self.head, self.neck, False

        reference_magnitude = self.error_threshold_px + (
            (magnitude - threshold)
            * (self.max_error_px - self.error_threshold_px)
            / max(1.0, maximum - threshold))
        strength = self._strength(reference_magnitude)
        # This is the C++ atan2(-error_y, error_x) joystick projection.
        horizontal = error_x / magnitude
        vertical = -error_y / magnitude
        self.head = max(self.head_minimum, min(
            self.head_maximum, self.head - horizontal * strength))
        self.neck = max(self.neck_minimum, min(
            self.neck_maximum, self.neck + vertical * strength))
        return self.head, self.neck, True

    def move_to_neutral(self, step_deg=1.0):
        self.head = self._approach(self.head, self.head_neutral, step_deg)
        self.neck = self._approach(self.neck, self.neck_neutral, step_deg)
        return self.head, self.neck

    @staticmethod
    def _approach(value, target, step):
        difference = target - value
        if abs(difference) <= step:
            return target
        return value + math.copysign(step, difference)
