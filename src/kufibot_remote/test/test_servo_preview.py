"""Preview trajectories follow the actual PWM driver's per-tick limiter."""
import base64
import copy
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kufibot_actuators.servo_motion import motion_profile
from kufibot_actuators.servo_node import ServoNode
from kufibot_interaction.joint_limits import NEUTRAL_ANGLES
from kufibot_interaction.mimics import evaluate
from kufibot_remote.node import RemoteController


def command_motion(interpolation='step'):
    return dict(id='travel', name='Travel', description='', revision=0, duration_ms=1000,
        interpolation=interpolation, keyframes=[dict(time_ms=0,joints=dict(NEUTRAL_ANGLES)),
        dict(time_ms=1000,joints={**NEUTRAL_ANGLES,'rightArm':65,'leftArm':110,'neck':100})])


def javascript_preview(motion, profile, initial):
    node=shutil.which('node')
    if not node:pytest.skip('Node.js is required')
    module=Path(__file__).parents[1]/'kufibot_remote/web/mimic-math.js'
    url='data:text/javascript;base64,'+base64.b64encode(module.read_bytes()).decode()
    script='''
      let data='';for await(const chunk of process.stdin)data+=chunk;
      const value=JSON.parse(data), api=await import(process.argv[1]);
      process.stdout.write(JSON.stringify(api.compileServoPreview(value.motion,value.profile,value.initial)));
    '''
    result=subprocess.run([node,'--input-type=module','-e',script,url],
        input=json.dumps(dict(motion=motion,profile=profile,initial=initial)),text=True,capture_output=True,check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('interpolation',['linear','step'])
@pytest.mark.parametrize('profile',[motion_profile(),motion_profile(.5,.1,20,40)])
def test_preview_matches_actual_driver_ticks_and_settles_after_final_command(interpolation,profile):
    motion=command_motion(interpolation)
    initial={**NEUTRAL_ANGLES,'rightArm':35,'leftArm':130}
    original=copy.deepcopy(motion)
    preview=javascript_preview(motion,profile,initial)
    node=ServoNode.__new__(ServoNode)
    node.startup_complete=True
    node.step_deg=profile['step_deg'];node.arm_step_deg=profile['arm_step_deg']
    node.current_angles=dict(initial);node.target_angles=evaluate(motion,0)
    node.min_pulse=500;node.max_pulse=2500;node.freq=50;node.driver=Mock()
    for time,position in zip(preview['times'],preview['positions']):
        if time and abs(time/profile['command_period_ms']-round(time/profile['command_period_ms']))<1e-7:
            node.target_angles=evaluate(motion,time)
        if time and abs(time/profile['step_delay_ms']-round(time/profile['step_delay_ms']))<1e-7:
            ServoNode._motion_tick(node)
        assert position==pytest.approx(node.current_angles,abs=1e-8)
    assert preview['duration_ms']>motion['duration_ms']
    assert preview['positions'][-1]==pytest.approx(motion['keyframes'][-1]['joints'])
    assert motion==original
    if interpolation=='step':
        # The final target is issued at 1 s; the arm still has to travel there.
        at=preview['times'].index(1000)
        assert preview['positions'][at]['rightArm']!=65


def test_first_pose_is_reached_from_current_robot_position_even_for_zero_duration():
    motion=command_motion();motion['keyframes']=motion['keyframes'][:1];motion['duration_ms']=0
    initial={**NEUTRAL_ANGLES,'leftArm':110}
    preview=javascript_preview(motion,motion_profile(),initial)
    assert preview['positions'][0]==initial
    assert preview['duration_ms']==3000
    assert preview['positions'][-1]==pytest.approx(NEUTRAL_ANGLES)


def test_bridge_reads_servo_runtime_parameters_without_commanding_hardware():
    node=RemoteController.__new__(RemoteController)
    node._control_period_sec=.04
    values=[SimpleNamespace(type=3,double_value=value) for value in (.6,.2,25)]
    RemoteController._servo_motion_received(node,SimpleNamespace(result=lambda:SimpleNamespace(values=values)))
    assert node.servo_motion['source']=='servo_node'
    assert node.servo_motion['degrees_per_second']['leftArm']==8
    assert node.servo_motion['degrees_per_second']['neck']==24
    assert node.servo_motion['command_period_ms']==40
    previous=node.servo_motion
    values[-1].double_value=0
    RemoteController._servo_motion_received(node,SimpleNamespace(result=lambda:SimpleNamespace(values=values)))
    assert node.servo_motion is previous
