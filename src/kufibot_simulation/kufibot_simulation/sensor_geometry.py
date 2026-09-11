"""Eye-mounted sensors using the same joint transforms and home meshes as the viewer."""
import math

import numpy as np

from kufibot_interaction.robot_model import joint_rotation, load_rig


def sensor_pose(angles, sensor, x=0.0, y=0.0, bearing=0.0, rig=None):
    """World position, optical direction and up vector; glTF converted to Z-up."""
    rig = rig or load_rig()
    mount = rig['sensors'][sensor]
    chain = []
    name = mount['parent']
    while name != 'body':
        spec = rig['joints'][name]
        chain.append((name, spec))
        name = spec['parent']
    transform = np.eye(4)
    for name, spec in reversed(chain):
        axis = np.asarray(spec['axis'], dtype=float)
        a = joint_rotation(spec, angles.get(name, spec['neutral_deg']))
        cross = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]],
                          [-axis[1], axis[0], 0]])
        local = np.eye(4)
        local[:3, :3] = (np.eye(3) + math.sin(a)*cross
                         + (1-math.cos(a))*(cross @ cross))
        local[:3, 3] = spec['pivot_m']
        transform = transform @ local
    b = math.radians(bearing)
    world = np.array([[math.cos(b), math.sin(b), 0],
                      [-math.sin(b), math.cos(b), 0], [0, 0, 1]])
    conversion = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    rotation = world @ conversion @ transform[:3, :3]
    position = world @ conversion @ (transform @ [*mount['position_m'], 1])[:3]
    position += [x, y, 0]
    forward = rotation @ [0, 0, -1]
    up = rotation @ [0, 1, 0]
    return dict(x=float(position[0]), y=float(position[1]), z=float(position[2]),
                bearing_deg=math.degrees(math.atan2(forward[0], forward[1])) % 360,
                pitch_deg=math.degrees(math.asin(float(np.clip(forward[2], -1, 1)))),
                direction=forward.tolist(), up=up.tolist())


class SensorWorld:
    """Windowless ray queries against the viewer's actual triangles, including floors."""
    def __init__(self, plan):
        from panda3d.core import (NodePath, CollisionNode, CollisionRay,
                                  CollisionTraverser, CollisionHandlerQueue, BitMask32)
        from .scene import build_home
        self.root = NodePath('sensor-world')
        build_home(self.root, plan)
        self.ray = CollisionRay()
        node = CollisionNode('lidar-ray')
        node.addSolid(self.ray)
        node.setFromCollideMask(BitMask32.allOn())
        node.setIntoCollideMask(BitMask32.allOff())
        self.queue = CollisionHandlerQueue()
        self.traverser = CollisionTraverser()
        self.traverser.addCollider(self.root.attachNewNode(node), self.queue)

    def raycast(self, pose, max_range=8.0, min_range=0.2):
        from .floorplan import RaycastHit
        origin = (pose['x'], pose['y'], pose['z'])
        self.ray.setOrigin(*origin)
        self.ray.setDirection(*pose['direction'])
        self.queue.clearEntries()
        self.traverser.traverse(self.root)
        self.queue.sortEntries()
        if self.queue.getNumEntries():
            point = self.queue.getEntry(0).getSurfacePoint(self.root)
            distance = math.dist(origin, point)
            if distance <= max_range:
                return RaycastHit(max(min_range, distance), True, hit=tuple(point))
        return RaycastHit(max_range, False)
