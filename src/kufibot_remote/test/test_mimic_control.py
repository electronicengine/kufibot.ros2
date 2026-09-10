import pytest
from kufibot_remote.control import Control
from kufibot_interaction.mimics import MimicStore
from kufibot_interaction.joint_limits import NEUTRAL_ANGLES


def make_control(tmp_path):
    clock=[10.0]; c=Control(clock=lambda:clock[0]);c.mimic_store=MimicStore(tmp_path/'m.json')
    c.mimic_store.save(dict(id='test',name='Test',description='',revision=0,duration_ms=1000,
        interpolation='linear',keyframes=[{'time_ms':0,'joints':dict(NEUTRAL_ANGLES)},
        {'time_ms':1000,'joints':{**NEUTRAL_ANGLES,'rightArm':65}}]))
    c.command('owner',{'type':'claim'});return c,clock


def test_playback_ownership_timing_and_snapshot(tmp_path):
    c,clock=make_control(tmp_path)
    command=dict(type='playMimic',id='test',revision=1)
    with pytest.raises(ValueError):c.command('other',command)
    c.command('owner',command)
    newer=c.mimic_store.get('test');newer['keyframes'][1]['joints']['rightArm']=20;c.mimic_store.save(newer)
    clock[0]+=.5;c.tick(.05,NEUTRAL_ANGLES);assert c.targets['rightArm']==40
    clock[0]+=.5;c.tick(.05,NEUTRAL_ANGLES);assert c.targets['rightArm']==65
    assert c.mimic_status['state']=='completed'
    with pytest.raises(ValueError):c.command('owner',command)


@pytest.mark.parametrize('command',[{'type':'stop'},{'type':'mode','mode':'ai'},
    {'type':'joint','name':'rightArm','value':20},{'type':'stopMimic'},
    {'type':'input','head_x':1}])
def test_explicit_cancellation(tmp_path,command):
    c,clock=make_control(tmp_path);c.command('owner',dict(type='playMimic',id='test',revision=1))
    c.command('owner',command);assert c.active_mimic is None


def test_disconnect_and_heartbeat(tmp_path):
    c,clock=make_control(tmp_path);c.command('owner',dict(type='playMimic',id='test',revision=1))
    clock[0]+=2.1;c.tick(.05,NEUTRAL_ANGLES);assert c.active_mimic is None
    c.command('owner',{'type':'claim'});c.command('owner',dict(type='playMimic',id='test',revision=1))
    c.release('owner');assert c.active_mimic is None
