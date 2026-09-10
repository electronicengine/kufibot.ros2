#!/usr/bin/env python3
"""Manual tool console with an optional automated kitchen scenario.

The human caller replaces only the remote AI provider. Physics, lidar,
camera, servo arbitration, motor driver and navigation are production nodes
with the normal simulation configuration. No network AI account is required.
"""
import argparse
import asyncio
from collections import deque
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

import cv2
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.signals import SignalHandlerOptions
from sensor_msgs.msg import Image
from std_msgs.msg import String
import yaml

from kufibot_actuators.servo_node import ServoNode
from kufibot_interaction.navigation_tools import NavigationTools
from kufibot_interaction.servo_arbiter import ServoArbiter
from kufibot_navigation.node import NavigationNode
from kufibot_simulation.sim_dc_motor_node import SimDcMotorNode
from kufibot_simulation.sim_servo_node import NullServoDriver
from kufibot_simulation.world_node import WorldNode
from navigation_demo_ui import ManualControl, Window

ROOT = Path(__file__).resolve().parents[1]
LEGS = [(0., .7), (0., .7), (-90., .6), (0., 1.),
        (0., 1.), (0., 1.), (0., .45), (90., 1.)]


class Session:
    def __init__(self):
        self.handlers = {}
        self.metadata = {}

    def tool(self, **metadata):
        def register(function):
            self.handlers[function.__name__] = function
            self.metadata[function.__name__] = metadata
            return function
        return register


class Demo(Node):
    def __init__(self, output):
        super().__init__('kitchen_demo')
        self.output = output
        self.session = Session()
        self.session_active, self.stopping = True, False
        self.ai_settings = {'provider': 'verasist'}
        self.navigation_camera_lock = asyncio.Lock()
        self.tools = NavigationTools(self)
        self.tools.register(self.session)
        self.tools.connected = True
        self.epoch = uuid.uuid4().hex
        self.authority = self.create_publisher(String, 'navigation/authority', 1)
        # The arbiter owns applied_mode; send its real command heartbeat.
        self.remote = self.create_publisher(String, 'remote/command', 1)
        self.create_timer(.1, self.heartbeat)
        self.world = {}
        self.live = self.observation = None
        self.trail = deque(maxlen=4000)
        self.events = deque(maxlen=100)
        self.image_count = 0
        self.phase = 'Bağlantı hazırlanıyor'
        self.create_subscription(String, 'simulation/world_state', self.on_world, 5)
        self.create_subscription(Image, 'camera/image_raw', self.on_camera, qos_profile_sensor_data)

    def heartbeat(self):
        self.remote.publish(String(data=json.dumps({'mode': 'ai', 'targets': {}})))
        self.authority.publish(String(data=json.dumps(dict(
            epoch=self.epoch, enabled=not self.stopping, owner=True,
            provider='verasist', mode='ai', reason='kitchen_demo'))))

    def reset_session(self):
        self.session = Session()
        self.tools.register(self.session)
        self.tools.connected = True
        self.epoch = uuid.uuid4().hex
        self.phase = 'Yeni oturum • sensör oku veya hareket aracı çağır'
        self.event('session', epoch=self.epoch)

    def on_world(self, msg):
        self.world = json.loads(msg.data)
        pose = self.world['pose']
        point = (pose['x'], pose['y'])
        if not self.trail or math.dist(point, self.trail[-1]) > .015:
            self.trail.append(point)

    def on_camera(self, msg):
        frame = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.step)
        self.live = frame[:, :msg.width * 3].reshape(msg.height, msg.width, 3).copy()

    def event(self, kind, **data):
        record = dict(time=round(time.monotonic(), 3), kind=kind, **data)
        line = json.dumps(record, ensure_ascii=False)
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
        self.events.append(record)
        console = dict(record)
        if kind == 'result':
            console['result'] = {key: value for key, value in data['result'].items()
                                 if key in ('status', 'reason', 'moved_m', 'turned_deg')}
        print(json.dumps(console, ensure_ascii=False), flush=True)

    async def _send_device_image(self, session, *, image_bytes, prompt, check, **kwargs):
        check()
        self.image_count += 1
        filename = f'observation-{self.image_count:02d}.jpg'
        (self.output / filename).write_bytes(image_bytes)
        metadata = json.loads(prompt)['navigation_observation']
        (self.output / filename.replace('.jpg', '.json')).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        self.observation = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.event('image', file=filename, observation_id=metadata['id'])
        return {'status': 'success'}


async def scenario(demo, plan):
    deadline = time.monotonic() + 20
    while not (demo.tools.state.get('enabled') and demo.world
               and demo.tools.tasks.service_is_ready() and demo.tools.steps.server_is_ready()):
        if time.monotonic() > deadline:
            raise RuntimeError(f'Navigasyon hazır olmadı: {demo.tools.state}')
        await asyncio.sleep(.1)

    async def call(name, **args):
        demo.event('call', tool=name, arguments=args)
        result = await demo.session.handlers[name](**args)
        demo.event('result', tool=name, result=result)
        if result.get('status') not in ('ok', 'completed'):
            raise RuntimeError(f'{name}: {result.get("reason", result)}')
        await asyncio.sleep(.4)  # Executor thread keeps heartbeats alive.
        return result

    demo.phase = 'Başlangıç taraması'
    for index, (angle, distance) in enumerate(LEGS):
        demo.phase = f'Mutfak rotası • Adım {index + 1}/{len(LEGS)}'
        await call('goto', angle_deg=angle, distance_m=distance)
    demo.phase = 'Mutfak görüntüsü kontrol ediliyor'
    await call('look_at', angle_deg=0.)
    pose = demo.world['pose']
    if plan.point_in_room(pose['x'], pose['y']) != 'kitchen':
        raise RuntimeError(f'Robot mutfağa ulaşmadı: {pose}')
    demo.phase = 'Tamamlandı • Robot mutfakta'
    demo.event('completed', pose=pose, images=demo.image_count)


async def run(args):
    output = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix='kufibot-kitchen-'))
    output.mkdir(parents=True, exist_ok=True)
    namespace = '/kitchen_' + uuid.uuid4().hex[:10]
    config = yaml.safe_load((ROOT / 'src/kufibot_simulation/config/simulation.yaml').read_text())
    config['world_node']['ros__parameters'].update(camera_width=320, camera_height=240)
    if not args.auto:
        config['navigation_node']['ros__parameters']['llm_timeout_sec'] = 3600.0
    with tempfile.TemporaryDirectory(prefix='kufibot-demo-config-') as temp:
        config_path = Path(temp) / 'sim.yaml'
        config_path.write_text(yaml.safe_dump({namespace+'/'+k: v for k, v in config.items()}))
        # asyncio owns Ctrl+C so stop/disconnect can be published before ROS
        # destroys its context. The executor remains responsive in its thread.
        rclpy.init(args=['--ros-args', '-r', '__ns:='+namespace, '--params-file', str(config_path)],
                   signal_handler_options=SignalHandlerOptions.NO)
    nodes, window, thread, job = [], None, None, None
    executor = SingleThreadedExecutor()
    demo = control = None
    try:
        for factory in (NavigationNode, ServoArbiter, lambda: ServoNode(driver_factory=NullServoDriver),
                        SimDcMotorNode, WorldNode, lambda: Demo(output)):
            node = factory()
            nodes.append(node)
            executor.add_node(node)
        world, demo = nodes[-2:]
        control = ManualControl(demo, args.goal)
        control.automatic = args.auto
        if not args.headless:
            window = Window(demo, world.floorplan, control)
        thread = threading.Thread(target=executor.spin, daemon=True)
        thread.start()
        print(f'Görüntü ve kayıtlar: {output}', flush=True)
        demo.event('goal', text=args.goal, mode='auto' if args.auto else 'manual')
        if args.auto:
            job = asyncio.create_task(scenario(demo, world.floorplan))
        reported = False
        failed = False
        while True:
            if not args.auto and demo.phase == 'Bağlantı hazırlanıyor' and demo.tools.state.get('enabled'):
                demo.phase = 'Hazır • sensör oku veya hareket aracı çağır'
            if window and not window.draw():
                break
            if job and job.done() and not reported:
                control.automatic = False
                try:
                    job.result()
                except Exception as error:
                    failed = True
                    demo.phase = 'Durduruldu: ' + str(error)
                    demo.event('error', detail=str(error))
                reported = True
                if window:
                    window.draw()
                    window.pg.image.save(window.screen, str(output / 'summary.png'))
                if args.headless or args.exit_when_done:
                    break
            await asyncio.sleep(.025)
        return 1 if failed else 0
    finally:
        if window:
            window.pg.image.save(window.screen, str(output / 'summary.png'))
        if demo:
            demo.stopping = True
            demo.tools.disconnect()
            demo.heartbeat()
        if job and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        if control:
            await control.close()
        if thread:
            await asyncio.sleep(.2)
        executor.shutdown(timeout_sec=3)
        if thread:
            thread.join(timeout=3)
        if demo:
            demo.tools.steps.destroy()
        for node in reversed(nodes):
            node.destroy_node()
        if window:
            window.pg.quit()
        rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description='Oda → koridor → mutfak görsel navigasyon demosu')
    parser.add_argument('--auto', action='store_true', help='İsteğe bağlı hazır mutfak rotasını otomatik yürüt')
    parser.add_argument('--goal', default='Koridordan geçerek mutfağa git', help='Manuel panelde gösterilen görev')
    parser.add_argument('--headless', action='store_true', help='Pencere açmadan aynı rotayı çalıştır')
    parser.add_argument('--exit-when-done', action='store_true', help='Bitişte pencereyi kapat')
    parser.add_argument('--output-dir', help='JPEG, gözlem JSON ve events.jsonl kayıt dizini')
    args = parser.parse_args()
    if (args.headless or args.exit_when_done) and not args.auto:
        parser.error('--headless ve --exit-when-done için --auto gerekli; varsayılan mod manuel paneldir')
    os.environ.setdefault('ROS_LOG_DIR', '/tmp/kufibot-demo-ros')
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
