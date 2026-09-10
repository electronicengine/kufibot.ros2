"""Metre-scale home geometry and the shared STL-derived articulated robot."""
import math

from panda3d.core import (Geom, GeomNode, GeomTriangles, GeomVertexData,
                         GeomVertexFormat, GeomVertexWriter)
from kufibot_interaction.joint_limits import JOINT_LIMITS, NEUTRAL_ANGLES
from kufibot_interaction.robot_model import load_rig


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


def furniture(parent, obstacle):
    """Recognizable furniture, contained in the plan's collision footprint."""
    x0, y0, x1, y1 = obstacle.rect
    w, d = x1-x0, y1-y0
    if round(obstacle.yaw_deg / 90) % 2:
        w, d = d, w
    root = parent.attachNewNode(obstacle.id)
    root.setPos((x0+x1)/2, (y0+y1)/2, 0)
    root.setH(-obstacle.yaw_deg)
    color = tuple(c/255 for c in obstacle.color)
    wood, dark = (.42,.27,.15), (.08,.10,.12)
    kind = obstacle.kind or (obstacle.id + ' ' + obstacle.label).lower()
    def part(name, x,y,z, a,b,c, tint=color):
        return box(root, name, (x*w,y*d,z), (a*w,b*d,c), tint)
    def legs(height):
        for x in (-.4,.4):
            for y in (-.4,.4):
                part('leg',x,y,height/2,.08,.08,height,wood)
    if any(k in kind for k in ('sofa','koltuk')):
        legs(.18)
        part('seat',0,0,.3,.96,.94,.24)
        part('backrest',0,.4,.64,.96,.16,.55)
        for x in (-.44,.44):
            part('armrest',x,0,.48,.12,.96,.3)
        for x in (-.22,.22):
            part('cushion',x,-.05,.45,.4,.68,.10,tuple(min(1,c*1.25) for c in color))
        height=.92
    elif any(k in kind for k in ('chair','sandalye')):
        legs(.43)
        part('seat',0,0,.45,.96,.96,.06)
        for x in (-.4,.4):
            part('back-support',x,.4,.65,.08,.08,.48,wood)
        part('backrest',0,.4,.78,.9,.12,.25)
        height=.91
    elif any(k in kind for k in ('tv','television','televizyon')):
        part('console',0,0,.25,.96,.94,.5,wood)
        part('stand',0,0,.55,.18,.4,.1,dark)
        part('television',0,.1,.95,.95,.15,.7,dark)
        part('screen',0,.015,.95,.87,.025,.60,(.16,.36,.51))
        height=1.3
    elif any(k in kind for k in ('cabinet','dolap')):
        part('cabinet',0,0,.85,.98,.98,1.7)
        for x in (-.24,.24):
            part('door',x,-.49,.87,.46,.025,1.57,wood)
        for x in (-.05,.05):
            part('handle',x,-.51,.85,.025,.025,.18,dark)
        height=1.7
    else:
        coffee = any(k in kind for k in ('coffee','sehpa','sehba'))
        height=.42 if coffee else .75
        legs(height-.05)
        part('tabletop',0,0,height-.025,.98,.98,.05,wood)
        if any(k in kind for k in ('desk','bilgisayar')):
            part('monitor-stand',0,.22,.85,.08,.1,.2,dark)
            part('monitor',0,.25,1.06,.55,.07,.34,dark)
            part('display',0,.21,1.06,.49,.015,.28,(.15,.4,.6))
            part('keyboard',0,-.15,.79,.45,.18,.025,dark)
            height=1.23
    # Collision observer uses a separate bound, not any decorative component.
    bound = root.attachNewNode('bounds')
    bound.setZ(height/2)
    return bound, (w/2,d/2,height/2)


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
        blockers.append(furniture(root, obstacle))
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
    """STL-derived GLB, sharing servo pivots and calibration with the editor."""
    width_m = .32
    height_m = load_rig()['height_m']
    sensor_height_m = .285
    sensor_spacing_m = .06
    model_scale = 1.0
    neck_up_degrees_per_servo_degree = .35
    wheel_radius = .10

    def __init__(self, parent):
        import gltf
        from panda3d.core import NodePath
        from kufibot_interaction.robot_model import MODEL_DIRECTORY, load_rig
        self.rig = load_rig()
        self.root = parent.attachNewNode('robot')
        self.model = NodePath(gltf.load_model(str(MODEL_DIRECTORY / 'robot.glb')))
        self.model.reparentTo(self.root)
        # Panda fixed/auto lighting uses COLOR_0 without a diffuse-material override.
        self.model.setMaterialOff(1)
        self.joints = {name: self.model.find('**/' + name) for name in self.rig['joints']}
        if any(node.isEmpty() for node in self.joints.values()):
            raise ValueError('Robot model is missing servo nodes')
        self.wheels = [self.model.find('**/' + name) for name in self.rig.get('wheels', {})]
        self.apply_joints(NEUTRAL_ANGLES)

    def apply_joints(self, angles):
        from panda3d.core import Quat, Vec3
        from kufibot_interaction.robot_model import joint_rotation
        for name, pivot in self.joints.items():
            spec = self.rig['joints'][name]
            angle = joint_rotation(spec, angles.get(name, spec['neutral_deg']))
            # glTF (x,y,z) -> Panda3D (x,-z,y).
            x, y, z = spec['axis']
            rotation = Quat()
            rotation.setFromAxisAngleRad(angle, Vec3(x, -z, y))
            pivot.setQuat(rotation)

    def animate_wheels(self, linear, angular, separation, dt):
        # Roll the STL's individual rollers around their own axle centers.
        for node, spec in zip(self.wheels, self.rig.get('wheels', {}).values()):
            speed = linear + angular*separation/2 if spec['side'] == 'left' else linear - angular*separation/2
            node.setP((node.getP()-math.degrees(speed*dt/spec['radius_m'])) % 360)
