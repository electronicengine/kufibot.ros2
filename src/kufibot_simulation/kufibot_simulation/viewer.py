"""Standalone third-person 3D observer; web/mobile retain control authority."""
import json
import math
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from direct.showbase.ShowBase import ShowBase
from direct.gui.OnscreenText import OnscreenText
from panda3d.core import AmbientLight, DirectionalLight, Point3, WindowProperties

from kufibot_interaction.joint_limits import JOINT_LIMITS, NEUTRAL_ANGLES
from .floorplan import FloorPlan
from .scene import Robot, build_home, segment_fraction


class ViewerNode(Node):
    def __init__(self):
        super().__init__('sim_viewer')
        self.state = None
        self.received_at = 0.0
        self.joints_deg = dict(NEUTRAL_ANGLES)
        self.create_subscription(String, 'simulation/world_state', self._on_state, 5)
        self.create_subscription(JointState, 'servo/joint_states', self._on_joints, 10)

    def _on_state(self, msg):
        try:
            data = json.loads(msg.data)
            if not all(math.isfinite(data['pose'][key]) for key in ('x', 'y', 'theta_deg')):
                return
        except (ValueError, TypeError, KeyError):
            return
        self.state, self.received_at = data, time.monotonic()

    def _on_joints(self, msg):
        for name, position in zip(msg.name, msg.position):
            if name in JOINT_LIMITS and math.isfinite(position):
                self.joints_deg[name] = math.degrees(position)


class Viewer(ShowBase):
    def __init__(self, node, **kwargs):
        super().__init__(**kwargs)
        self.node = node
        self.disableMouse()
        self.setBackgroundColor(.055, .075, .10)
        self.camLens.setFov(70)
        self.camLens.setNearFar(.025, 100)
        self.robot = Robot(self.render)
        self.home, self.plan_path, self.plan = None, None, None
        self.blockers = []
        self.captured = False
        self.map_mode = False
        self.yaw_offset, self.pitch, self.distance = 0.0, 22.0, 1.4
        self.follow_yaw = None
        self.target = None
        self.last_frame = time.monotonic()
        ambient = AmbientLight('ambient')
        ambient.setColor((.48,.48,.52,1))
        self.render.setLight(self.render.attachNewNode(ambient))
        sun = DirectionalLight('sun')
        sun.setColor((.85,.82,.75,1))
        light = self.render.attachNewNode(sun)
        light.setHpr(-35,-55,0)
        self.render.setLight(light)
        self.render.setShaderAuto()
        self.hint = OnscreenText(text='', pos=(-1.28,.92), scale=.043,
                                 align=0, mayChange=True, fg=(.92,.95,1,1))
        self.accept('mouse1', self.capture, [True])
        self.accept('escape', self.capture, [False])
        self.accept('window-event', self.window_event)
        self.accept('wheel_up', self.zoom, [-.15])
        self.accept('wheel_down', self.zoom, [.15])
        self.accept('r', self.reset_camera)
        self.accept('m', self.toggle_map)
        self.taskMgr.add(self.update, 'ros-and-scene')

    def capture(self, enabled):
        if not self.win or not hasattr(self.win, 'requestProperties'):
            return
        self.captured = enabled
        props = WindowProperties()
        props.setCursorHidden(enabled)
        self.win.requestProperties(props)
        if enabled:
            self.win.movePointer(0, self.win.getXSize()//2, self.win.getYSize()//2)

    def window_event(self, window):
        if window and hasattr(window, 'getProperties') and not window.getProperties().getForeground():
            self.capture(False)

    def zoom(self, delta):
        self.distance = max(.55, min(4.0, self.distance+delta))

    def reset_camera(self):
        self.yaw_offset, self.pitch, self.distance = 0.0, 22.0, 1.4

    def toggle_map(self):
        self.map_mode = not self.map_mode

    def load_plan(self, path):
        if not path or path == self.plan_path:
            return
        try:
            plan = FloorPlan.load(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.node.get_logger().error(f'Ev plani okunamadi: {error}')
            self.plan_path = path  # avoid logging the same failure every frame
            return
        if self.home:
            self.home.removeNode()
        self.home, self.blockers = build_home(self.render, plan)
        self.plan, self.plan_path = plan, path

    def update(self, task):
        now = time.monotonic()
        dt, self.last_frame = min(.1, now-self.last_frame), now
        if not rclpy.ok():
            self.taskMgr.stop()
            return task.done
        # Bounded non-blocking ROS work keeps the render loop responsive.
        for _ in range(8):
            rclpy.spin_once(self.node, timeout_sec=0.0)
        if self.captured and self.win:
            pointer = self.win.getPointer(0)
            cx, cy = self.win.getXSize()//2, self.win.getYSize()//2
            dx, dy = pointer.getX()-cx, pointer.getY()-cy
            # Pointer coordinates increase right/down.  Invert them so the
            # camera looks in the same direction as the pointer motion:
            # right/left rotates right/left; up/down tilts down/up.
            self.yaw_offset = (self.yaw_offset - max(-100,min(100,dx))*.15) % 360
            self.pitch = max(5, min(75, self.pitch - max(-100,min(100,dy))*.12))
            self.win.movePointer(0,cx,cy)
        state = self.node.state
        if state is None:
            self.robot.root.hide()
            self.hint.setText('Simulasyon verisi bekleniyor...')
            return task.cont
        self.robot.root.show()
        self.load_plan(state.get('floorplan_file'))
        pose = state['pose']
        heading = pose['theta_deg']
        self.robot.root.setPos(pose['x'],pose['y'],0)
        self.robot.root.setH(-heading)
        self.robot.apply_joints(self.node.joints_deg)
        fresh = now-self.node.received_at < .5
        twist = state.get('applied_twist', {})
        if fresh:
            self.robot.animate_wheels(twist.get('linear_mps',0), twist.get('angular_rps',0),
                                     state.get('wheel_separation_m',.2), dt)
        target = Point3(pose['x'], pose['y'], Robot.height_m / 2)
        alpha = 1-math.exp(-dt/.12)
        if self.target is None:
            self.target, self.follow_yaw = target, heading
        self.target += (target-self.target)*alpha
        self.follow_yaw += ((heading-self.follow_yaw+180)%360-180)*alpha
        if self.map_mode and self.plan:
            points = [p for wall in self.plan.walls for p in (wall.a,wall.b)]
            if points:
                xs, ys = zip(*points)
                center = Point3((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, 0)
                span = max(max(ys)-min(ys), (max(xs)-min(xs))/self.getAspectRatio(), 2)
                height = span/(2*math.tan(math.radians(self.camLens.getVfov()/2))) + 3
                self.camera.setPos(center+Point3(0,0,height))
                self.camera.setHpr(0,-90,0)
        else:
            yaw, pitch = math.radians(self.follow_yaw+self.yaw_offset), math.radians(self.pitch)
            offset = Point3(-math.sin(yaw)*math.cos(pitch),
                            -math.cos(yaw)*math.cos(pitch), math.sin(pitch))*self.distance
            desired = self.target+offset
            fraction = 1.0
            for item, extents in self.blockers:
                start = item.getRelativePoint(self.render,self.target)
                end = item.getRelativePoint(self.render,desired)
                fraction = min(fraction, segment_fraction(start,end,extents))
            self.camera.setPos(self.target+offset*max(.02, fraction-.02))
            self.camera.lookAt(self.target)
        status = 'Web / mobil kumanda' if fresh else 'Baglanti kesildi - goruntu duraklatildi'
        self.hint.setText(f'{status}\nTikla: fare | Esc: birak | Tekerlek: zoom | R: arkaya | M: harita')
        return task.cont


def main(args=None):
    rclpy.init(args=args)
    node = ViewerNode()
    app = None
    try:
        app = Viewer(node)
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        if app:
            app.capture(False)
            app.destroy()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
