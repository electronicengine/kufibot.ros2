"""Procedural, metre-scale Panda3D scene. No external model assets required."""
import math

from panda3d.core import (Geom, GeomNode, GeomTriangles, GeomVertexData,
                         GeomVertexFormat, GeomVertexWriter)
from kufibot_interaction.joint_limits import JOINT_LIMITS, NEUTRAL_ANGLES


def box(parent, name, position, size, color):
    data = GeomVertexData(name, GeomVertexFormat.getV3n3(), Geom.UHStatic)
    vertices, normals = GeomVertexWriter(data, 'vertex'), GeomVertexWriter(data, 'normal')
    triangles = GeomTriangles(Geom.UHStatic)
    faces = [((1, 0, 0), [(1,-1,-1),(1,1,-1),(1,1,1),(1,-1,1)]),
             ((-1,0,0), [(-1,1,-1),(-1,-1,-1),(-1,-1,1),(-1,1,1)]),
             ((0,1,0), [(1,1,-1),(-1,1,-1),(-1,1,1),(1,1,1)]),
             ((0,-1,0), [(-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1)]),
             ((0,0,1), [(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]),
             ((0,0,-1), [(-1,1,-1),(1,1,-1),(1,-1,-1),(-1,-1,-1)])]
    for face, (normal, points) in enumerate(faces):
        for point in points:
            vertices.addData3f(*(point[i] * size[i] / 2 for i in range(3)))
            normals.addData3f(*normal)
        i = face * 4
        triangles.addVertices(i, i+1, i+2)
        triangles.addVertices(i, i+2, i+3)
    geom = Geom(data)
    geom.addPrimitive(triangles)
    node = GeomNode(name)
    node.addGeom(geom)
    result = parent.attachNewNode(node)
    result.setPos(*position)
    result.setColor(*color, 1)
    return result


def build_home(parent, plan):
    root = parent.attachNewNode('home')
    # Bounds are also used by the camera's segment collision query.
    blockers = []
    for room in plan.rooms.values():
        x0, y0, x1, y1 = room.rect
        box(root, room.id, ((x0+x1)/2, (y0+y1)/2, -.025),
            (x1-x0, y1-y0, .05), tuple(c/255 for c in room.color))
    for i, wall in enumerate(plan.walls):
        ax, ay = wall.a
        bx, by = wall.b
        length = math.hypot(bx-ax, by-ay)
        item = box(root, f'wall-{i}', ((ax+bx)/2, (ay+by)/2, 1.2),
                   (length, .02, 2.4), (.65, .69, .73))
        item.setH(math.degrees(math.atan2(by-ay, bx-ax)))
        blockers.append((item, (length/2, .01, 1.2)))
    for obstacle in plan.obstacles:
        x0, y0, x1, y1 = obstacle.rect
        item = box(root, obstacle.id, ((x0+x1)/2, (y0+y1)/2, .4),
                   (x1-x0, y1-y0, .8), tuple(c/255 for c in obstacle.color))
        blockers.append((item, ((x1-x0)/2, (y1-y0)/2, .4)))
    return root, blockers


def segment_fraction(start, end, half_extents, margin=.08):
    """First intersection with an expanded local box, or 1 for no hit."""
    lo, hi = 0.0, 1.0
    for a, b, extent in zip(start, end, half_extents):
        extent += margin
        delta = b-a
        if abs(delta) < 1e-9:
            if abs(a) > extent:
                return 1.0
            continue
        near, far = sorted(((-extent-a)/delta, (extent-a)/delta))
        lo, hi = max(lo, near), min(hi, far)
        if lo > hi:
            return 1.0
    return max(0.0, lo)


class Robot:
    # The unscaled procedural model is 0.74 m tall.  Scale it to the physical
    # robot's approximately 28 cm overall height while preserving metre-based
    # world placement and motor animation.
    height_m = .28
    _unscaled_height_m = .74
    model_scale = height_m / _unscaled_height_m
    neck_up_degrees_per_servo_degree = .35
    wheel_radius = .10

    def __init__(self, parent):
        self.root = parent.attachNewNode('robot')
        self.root.setScale(self.model_scale)
        cyan, metal = (.08, .60, .72), (.65, .69, .72)
        box(self.root, 'body', (0, 0, .27), (.26, .26, .28), cyan)
        box(self.root, 'front-panel', (0, .135, .28), (.18, .015, .13), (.04,.08,.10))
        self.wheels = []
        for side in (-1, 1):
            pivot = self.root.attachNewNode('wheel')
            pivot.setPos(side*.10, 0, self.wheel_radius)
            # Twelve tread blocks form a rounded wheel, with visible rotating spokes.
            for i in range(12):
                a = i * math.tau / 12
                tread = box(pivot, 'tread', (0, math.sin(a)*.085, math.cos(a)*.085),
                            (.08, .052, .03), (.06,.07,.08))
                tread.setP(-math.degrees(a))
            box(pivot, 'spoke', (side*.042,0,0), (.008,.15,.025), metal)
            self.wheels.append(pivot)
        self.joints = {}
        for name, side in [('leftArm', -1), ('rightArm', 1)]:
            pivot = self.root.attachNewNode(name)
            pivot.setPos(side*.15, 0, .37)
            box(pivot, 'arm', (side*.025, 0, -.10), (.045,.06,.22), metal)
            box(pivot, 'hand', (side*.025,.025,-.21), (.07,.10,.045), cyan)
            self.joints[name] = pivot
        neck = self.root.attachNewNode('neck')
        neck.setPos(0,0,.41)
        box(neck, 'neck-link', (0,0,.09), (.055,.055,.18), metal)
        head = neck.attachNewNode('head')
        head.setPos(0,0,.19)
        box(head, 'head-bar', (0,0,0), (.27,.08,.065), cyan)
        self.joints.update(neck=neck, headLeftRight=head)
        for name, side in [('eyeLeft', -1), ('eyeRight', 1)]:
            pivot = head.attachNewNode(name)
            pivot.setPos(side*.075,.025,0)
            box(pivot, 'eye-shell', (0,.02,0), (.125,.10,.10), metal)
            box(pivot, 'lens', (0,.075,0), (.085,.012,.068), (.015,.025,.035))
            box(pivot, 'glint', (-.018,.083,.018), (.018,.003,.014), (.4,.85,1))
            self.joints[name] = pivot
        self.apply_joints(NEUTRAL_ANGLES)

    def apply_joints(self, angles):
        for name, pivot in self.joints.items():
            low, high = JOINT_LIMITS[name]
            value = angles.get(name, NEUTRAL_ANGLES[name])
            if not math.isfinite(value):
                continue
            value = max(low, min(high, value))
            if name == 'headLeftRight':
                pivot.setH(value - 90)
            elif name == 'leftArm':
                pivot.setP(180-value)
            elif name == 'rightArm':
                pivot.setP(value-10)
            elif name == 'neck':
                # The bottom stop is level; the neck only tilts the head up.
                pivot.setP(value * self.neck_up_degrees_per_servo_degree)
            else:
                pivot.setR(value-NEUTRAL_ANGLES[name])

    def animate_wheels(self, linear, angular, separation, dt):
        # World bearing increases clockwise, so left wheel travels farther in a right turn.
        for wheel, speed in zip(self.wheels, (linear + angular*separation/2,
                                               linear - angular*separation/2)):
            wheel.setP((wheel.getP()-math.degrees(speed*dt/self.wheel_radius)) % 360)
