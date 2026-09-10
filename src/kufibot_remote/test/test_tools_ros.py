"""Exercise tool calls, owner heartbeats and JPEG delivery through real ROS nodes."""
import asyncio, json, threading, time, uuid
from pathlib import Path
import yaml, tempfile
import rclpy
from rclpy.executors import SingleThreadedExecutor
from aiohttp.test_utils import TestClient, TestServer
from kufibot_navigation.node import NavigationNode
from kufibot_remote.node import RemoteController
from kufibot_remote.server import Server
from kufibot_interaction.servo_arbiter import ServoArbiter
from kufibot_simulation.world_node import WorldNode
from kufibot_simulation.sim_servo_node import NullServoDriver
from kufibot_actuators.servo_node import ServoNode
from kufibot_simulation.sim_dc_motor_node import SimDcMotorNode
async def main():
 ns='/verify_'+uuid.uuid4().hex[:8]
 config=yaml.safe_load((Path(__file__).resolve().parents[2] / 'kufibot_simulation/config/simulation.yaml').open())
 config['world_node']['ros__parameters'].update(camera_width=320, camera_height=240)
 with tempfile.NamedTemporaryFile(mode='w',suffix='.yaml') as f:
  yaml.safe_dump({ns+'/'+k:v for k,v in config.items()},f);f.flush()
  rclpy.init(args=['--ros-args','-r','__ns:='+ns,'--params-file',f.name])
 nodes=[NavigationNode(),ServoArbiter(),ServoNode(driver_factory=NullServoDriver),SimDcMotorNode(),WorldNode(),RemoteController()]
 remote=nodes[-1]
 executor=SingleThreadedExecutor()
 for n in nodes: executor.add_node(n)
 async def spin():
  while True:
   executor.spin_once(timeout_sec=0)
   await asyncio.sleep(.001)
 async def tick():
  while True:
   remote.tick(.05)
   await asyncio.sleep(.05)
 jobs=[asyncio.create_task(spin()),asyncio.create_task(tick())]
 try:
  async with TestClient(TestServer(Server(remote.control,remote.status,tool_call=remote.run_tool).app)) as client:
   ws=await client.ws_connect('/control')
   await ws.send_json({'type':'claim'})
   await ws.send_json({'type':'mode','mode':'tools'})
   async def pulse():
    while True:
     await ws.send_json({'type':'heartbeat'})
     await asyncio.sleep(.2)
   heart=asyncio.create_task(pulse())
   try:
    await asyncio.sleep(.5)
    for name,args in [('look_at',{'angle_deg':0}),('read_sensor_values',{'angle_deg':0,'sweep_deg':30}),('goto',{'distance_m':.2,'angle_deg':0})]:
     await asyncio.sleep(.7)
     await ws.send_json({'type':'tool','name':name,'arguments':args})
     async with asyncio.timeout(45):
      while True:
       msg=await ws.receive_json()
       if msg['type']=='toolResult':
        print(name, msg['result'].get('status'),msg['result'].get('reason'),flush=True)
        assert msg['result']['status']=='ok',msg
        assert msg['result'].get('image_url'),msg
        mapping = msg['result']['observation']['map']
        assert mapping['frame'] == 'startup_robot_pose'
        assert 'boundary_paths' in mapping
        assert mapping['origin'] == [0.0, 0.0]
        break
   finally: heart.cancel(); await asyncio.gather(heart,return_exceptions=True); await ws.close()
 finally:
  for j in jobs:j.cancel()
  await asyncio.gather(*jobs,return_exceptions=True)
  for n in reversed(nodes): n.destroy_node()
  executor.shutdown();rclpy.shutdown()
def test_websocket_tools_with_ros():
 asyncio.run(main())
