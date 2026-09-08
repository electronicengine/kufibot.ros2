"""Schematic first-person camera render: flat-shaded rooms and labeled doors.

Pure function of (floorplan, pose, bearing) so it can be unit tested without
rclpy or real image I/O. Not photorealistic: intended to give an LLM enough
visual structure to reason about rooms and doorways.
"""
import math

import cv2
import numpy as np

DEFAULT_FOV_DEG = 60.0
MAX_RANGE_M = 8.0
UNKNOWN_COLOR = (30, 30, 30)
FLOOR_COLOR = (45, 42, 40)
CEILING_COLOR = (70, 66, 60)
DOOR_COLOR = (220, 200, 40)


def _room_color(floorplan, room_id):
    room = floorplan.rooms.get(room_id)
    return tuple(room.color) if room else UNKNOWN_COLOR


def _obstacle_color(floorplan, obstacle_id):
    for obstacle in floorplan.obstacles:
        if obstacle.id == obstacle_id:
            return tuple(obstacle.color)
    return UNKNOWN_COLOR


def _angle_deg(ox, oy, tx, ty):
    return math.degrees(math.atan2(tx - ox, ty - oy)) % 360.0


def render(floorplan, x, y, bearing_deg, width=640, height=480,
           fov_deg=DEFAULT_FOV_DEG, pitch_deg=0.0):
    """Render the camera at a horizontal bearing and an upward pitch.

    The floor plan has no vertical geometry, so pitch is represented by moving
    the horizon.  Zero is level; positive values look upward and expose more
    ceiling.  It deliberately has no negative range: the real neck's bottom
    stop is its level, forward-looking position.
    """
    pitch_deg = max(0.0, min(45.0, float(pitch_deg)))
    horizon = min(int(height * .8), height // 2 + int(height * pitch_deg / 360.0))
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:horizon] = CEILING_COLOR
    frame[horizon:] = FLOOR_COLOR
    hit_distances = [MAX_RANGE_M] * width
    for col in range(width):
        ray_bearing = bearing_deg + (col / (width - 1) - 0.5) * fov_deg
        hit = floorplan.raycast(x, y, ray_bearing, max_range=MAX_RANGE_M)
        hit_distances[col] = hit.distance_m
        if not hit.valid:
            color = UNKNOWN_COLOR
        elif hit.obstacle_id:
            color = _obstacle_color(floorplan, hit.obstacle_id)
        else:
            color = _room_color(floorplan, hit.room_id)
        # Perspective illusion: closer walls fill more of the column's height.
        wall_h = int(min(height, height * (1.2 / max(hit.distance_m, 0.3))))
        top = max(0, horizon - wall_h // 2)
        shade = max(0.35, 1.0 - hit.distance_m / MAX_RANGE_M)
        frame[top:top + wall_h, col] = tuple(int(c * shade) for c in color)
    for door in floorplan.doors:
        cx, cy = (door.a[0] + door.b[0]) / 2.0, (door.a[1] + door.b[1]) / 2.0
        distance = math.hypot(cx - x, cy - y)
        if distance > MAX_RANGE_M:
            continue
        offset = ((_angle_deg(x, y, cx, cy) - bearing_deg + 180.0) % 360.0) - 180.0
        if abs(offset) > fov_deg / 2.0:
            continue
        col = int((offset / fov_deg + 0.5) * (width - 1))
        if hit_distances[col] + 0.3 < distance:
            continue  # a nearer wall occludes the doorway at this angle
        wall_h = int(min(height, height * (1.2 / max(distance, 0.3))))
        top = max(0, horizon - wall_h // 2)
        cv2.rectangle(frame, (max(0, col - 3), top), (min(width - 1, col + 3), top + wall_h),
                      DOOR_COLOR, 2)
        cv2.putText(frame, door.label, (max(0, col - 45), max(15, top - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, DOOR_COLOR, 1, cv2.LINE_AA)
    for obstacle in floorplan.obstacles:
        oxmin, oymin, oxmax, oymax = obstacle.rect
        cx, cy = (oxmin + oxmax) / 2.0, (oymin + oymax) / 2.0
        distance = math.hypot(cx - x, cy - y)
        if distance > MAX_RANGE_M:
            continue
        offset = ((_angle_deg(x, y, cx, cy) - bearing_deg + 180.0) % 360.0) - 180.0
        if abs(offset) > fov_deg / 2.0:
            continue
        col = int((offset / fov_deg + 0.5) * (width - 1))
        if hit_distances[col] + 0.3 < distance:
            continue  # a nearer wall/obstacle occludes this one at this angle
        wall_h = int(min(height, height * (1.2 / max(distance, 0.3))))
        top = max(0, horizon - wall_h // 2)
        cv2.putText(frame, obstacle.label, (max(0, col - 45), max(15, top - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, tuple(obstacle.color), 1, cv2.LINE_AA)
    return frame
