import copy
import pytest
from kufibot_interaction.mimics import MimicStore, RevisionConflict, evaluate, validate, default_store
from kufibot_interaction.joint_limits import NEUTRAL_ANGLES


def motion():
    return dict(id='wave', name='El salla', description='merhaba selam', revision=0,
                duration_ms=1000, interpolation='linear', keyframes=[
                    dict(time_ms=0,joints=dict(NEUTRAL_ANGLES)),
                    dict(time_ms=1000,joints={**NEUTRAL_ANGLES,'rightArm':65})])


def test_interpolation_and_hold():
    value=motion()
    assert evaluate(value,500)['rightArm']==40
    assert evaluate(value,2000)['rightArm']==65
    assert evaluate(value,-1)==NEUTRAL_ANGLES
    value['interpolation']='step'
    assert evaluate(value,999)['rightArm']==15
    assert evaluate(value,1000)['rightArm']==65


@pytest.mark.parametrize('edit',[
    lambda m:m['keyframes'][1].update(time_ms=0),
    lambda m:m['keyframes'][0].update(time_ms=1),
    lambda m:m['keyframes'][1]['joints'].update(rightArm=float('nan')),
    lambda m:m['keyframes'][1]['joints'].update(rightArm=73),
    lambda m:m['keyframes'][1]['joints'].pop('neck'),
    lambda m:m.update(id='../escape'),
    lambda m:m.update(duration_ms=True),
])
def test_invalid_records(edit):
    value=motion();edit(value)
    with pytest.raises(ValueError):validate(value)


def test_persistence_and_conflict(tmp_path):
    store=MimicStore(tmp_path/'mimics.json')
    value=store.save(motion()); assert value['revision']==1
    assert MimicStore(store.path).get('wave')==value
    with pytest.raises(RevisionConflict):store.save(motion())
    value['name']='Yeni'; assert store.save(value)['revision']==2
    assert not list(tmp_path.glob('.mimics-*'))


def test_legacy_and_user_refresh(tmp_path,monkeypatch):
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE',str(tmp_path/'mimics.json'))
    store=default_store()
    legacy=store.get('greeting')
    assert legacy['interpolation']=='step'
    assert evaluate(legacy,499)['rightArm']==65
    assert evaluate(legacy,500)['rightArm']==40
    from kufibot_interaction.expression_engine import ExpressionLibrary
    from pathlib import Path
    from kufibot_interaction import mimics
    root=Path(mimics.__file__).with_name('expression_defaults')
    library=ExpressionLibrary(root/'gesture_config.json',root/'motion_definitions.json',root/'joint_angles.json')
    store.save(motion()); assert library.refresh_users()
    active=copy.deepcopy(library.motions['wave'])
    value=store.get('wave');value['keyframes'][1]['joints']['rightArm']=45;store.save(value)
    library.refresh_users()
    assert active['keyframe_motion']['keyframes'][1]['joints']['rightArm']==65
    assert library.motions['wave']['keyframe_motion']['keyframes'][1]['joints']['rightArm']==45
    assert library.classify('selam')=='wave'
