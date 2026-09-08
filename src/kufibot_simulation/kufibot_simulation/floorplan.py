"""Pure-Python 2D home model: walls, doors and rooms; raycasting and clearance.

No ROS/rclpy dependency so it can be unit tested directly. Coordinates are
meters in a plane where bearing 0 deg points +y (north) and 90 deg points +x
(east), matching the compass convention used by kufibot_navigation.
"""
import json
import math
from dataclasses import dataclass
from pathlib import Path


def _bearing_to_dir(bearing_deg):
    rad = math.radians(bearing_deg)
    return math.sin(rad), math.cos(rad)


def _segment_ray_hit(ox, oy, dx, dy, ax, ay, bx, by):
    """Distance along the ray (ox,oy)+(dx,dy) to segment (a, b), or None."""
    sx, sy = bx - ax, by - ay
    denom = dx * sy - dy * sx
    if abs(denom) < 1e-12:
        return None
    qpx, qpy = ax - ox, ay - oy
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * dy - qpy * dx) / denom
    return t if t >= 0.0 and 0.0 <= u <= 1.0 else None


def _point_segment_distance(px, py, ax, ay, bx, by):
    sx, sy = bx - ax, by - ay
    length_sq = sx * sx + sy * sy
    if length_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * sx + (py - ay) * sy) / length_sq))
    return math.hypot(px - (ax + t * sx), py - (ay + t * sy))


def _rect_segments(rect):
    """The four edges of an axis-aligned rect, as (a, b) segment pairs."""
    xmin, ymin, xmax, ymax = rect
    return [
        ((xmin, ymin), (xmax, ymin)),
        ((xmax, ymin), (xmax, ymax)),
        ((xmax, ymax), (xmin, ymax)),
        ((xmin, ymax), (xmin, ymin)),
    ]


@dataclass
class Room:
    id: str
    label: str
    color: tuple
    rect: tuple  # (xmin, ymin, xmax, ymax)


@dataclass
class Wall:
    a: tuple
    b: tuple
    room_id: str


@dataclass
class Door:
    id: str
    label: str
    a: tuple
    b: tuple
    connects: tuple


@dataclass
class Obstacle:
    id: str
    label: str
    color: tuple
    rect: tuple  # (xmin, ymin, xmax, ymax), furniture or any other in-room blocker


@dataclass
class RaycastHit:
    distance_m: float
    valid: bool
    room_id: str = None
    hit: tuple = None
    obstacle_id: str = None


class FloorPlan:
    def __init__(self, rooms, walls, doors, obstacles=None, start_pose=None):
        self.rooms = {room.id: room for room in rooms}
        self.walls = walls
        self.doors = doors
        self.obstacles = obstacles or []
        self.start_pose = start_pose or {'x': 0.0, 'y': 0.0, 'theta_deg': 0.0}

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        rooms = [Room(id=r['id'], label=r['label'], color=tuple(r['color']),
                      rect=tuple(r['rect'])) for r in data['rooms']]
        walls = [Wall(a=tuple(w['a']), b=tuple(w['b']), room_id=w['room_id'])
                 for w in data['walls']]
        doors = [Door(id=d['id'], label=d['label'], a=tuple(d['a']), b=tuple(d['b']),
                      connects=tuple(d['connects'])) for d in data['doors']]
        obstacles = [Obstacle(id=o['id'], label=o['label'], color=tuple(o['color']),
                               rect=tuple(o['rect'])) for o in data.get('furniture', [])]
        return cls(rooms, walls, doors, obstacles, data.get('start_pose'))

    def raycast(self, x, y, bearing_deg, max_range=8.0, min_range=0.2):
        """Nearest wall or furniture item along bearing_deg; doors are open gaps."""
        dx, dy = _bearing_to_dir(bearing_deg)
        best_t, best_room_id, best_obstacle_id = None, None, None
        for wall in self.walls:
            t = _segment_ray_hit(x, y, dx, dy, wall.a[0], wall.a[1], wall.b[0], wall.b[1])
            if t is not None and t <= max_range and (best_t is None or t < best_t):
                best_t, best_room_id, best_obstacle_id = t, wall.room_id, None
        for obstacle in self.obstacles:
            for a, b in _rect_segments(obstacle.rect):
                t = _segment_ray_hit(x, y, dx, dy, a[0], a[1], b[0], b[1])
                if t is not None and t <= max_range and (best_t is None or t < best_t):
                    best_t, best_room_id, best_obstacle_id = t, None, obstacle.id
        if best_t is None:
            return RaycastHit(distance_m=max_range, valid=False)
        distance = max(min_range, best_t)
        return RaycastHit(distance_m=distance, valid=True, room_id=best_room_id,
                           hit=(x + dx * best_t, y + dy * best_t), obstacle_id=best_obstacle_id)

    def doors_on_ray(self, x, y, bearing_deg, max_range=8.0):
        """Doors this ray passes through before max_range, nearest first."""
        dx, dy = _bearing_to_dir(bearing_deg)
        hits = []
        for door in self.doors:
            t = _segment_ray_hit(x, y, dx, dy, door.a[0], door.a[1], door.b[0], door.b[1])
            if t is not None and t <= max_range:
                hits.append((t, door))
        hits.sort(key=lambda item: item[0])
        return hits

    def clearance(self, x, y):
        """Distance from (x, y) to the nearest wall or furniture edge; doors never block."""
        segments = [(w.a, w.b) for w in self.walls]
        segments.extend(edge for obstacle in self.obstacles for edge in _rect_segments(obstacle.rect))
        return min((_point_segment_distance(x, y, a[0], a[1], b[0], b[1])
                    for a, b in segments), default=math.inf)

    def point_in_room(self, x, y):
        for room in self.rooms.values():
            xmin, ymin, xmax, ymax = room.rect
            if xmin <= x <= xmax and ymin <= y <= ymax:
                return room.id
        return None
