"""All wrist/claw geometry must follow its arm, retaining the assembly shape."""
import importlib.util
import json
from pathlib import Path
import struct

import numpy as np
import pytest

from kufibot_interaction.robot_model import MODEL_DIRECTORY, load_rig


def test_mesh_paint_and_joint_parent_hierarchy_are_preserved():
    data=(MODEL_DIRECTORY/'robot.glb').read_bytes()
    size=struct.unpack_from('<I',data,12)[0]
    document=json.loads(data[20:20+size])
    nodes={node['name']:node for node in document['nodes']}
    for name,spec in load_rig()['joints'].items():
        parent=nodes[spec['parent']]
        assert next(i for i,n in enumerate(document['nodes']) if n['name']==name) in parent['children']
        assert any(document['nodes'][i].get('name')==name+'_mesh' for i in nodes[name]['children'])
    for mesh in document['meshes']:
        assert 'COLOR_0' in mesh['primitives'][0]['attributes'],mesh['name']


def test_distal_claw_classification_does_not_take_body_or_shoulder_panels():
    pytest.importorskip('trimesh')
    path=Path(__file__).resolve().parents[3]/'tools/build_robot_model.py'
    spec=importlib.util.spec_from_file_location('build_robot_model',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    claw=np.array([[-230.3,44.5,120.4],[-183.9,90.6,158.4]])
    assert module.distal_arm_attachment(claw)=='leftArm'
    assert module.distal_arm_attachment(np.array([[-167.9,93.7,150.8],[-167.9,102.2,158.8]])) is None
    assert module.distal_arm_attachment(np.array([[-183.3,87.3,15.8],[-167.9,244.7,156.7]])) is None


def test_both_hands_have_no_fixed_outboard_fingertips_left_on_body():
    trimesh=pytest.importorskip('trimesh')
    scene=trimesh.load_scene(MODEL_DIRECTORY/'robot.glb')
    rig=load_rig();scale=rig['scale_m_per_source_unit']
    def raw(name):
        matrix,_=scene.graph.get(name+'_mesh')
        world=trimesh.transform_points(scene.geometry[name+'_geometry'].vertices,matrix)
        return np.column_stack([world[:,0]/scale-103.086,world[:,2]/scale+168,world[:,1]/scale-52.595539])
    body=raw('body')
    left=(body[:,0]<-185)&(body[:,1]<100)&(body[:,2]>88)&(body[:,2]<165)
    right=(body[:,0]>-20)&(body[:,1]<100)&(body[:,2]>88)&(body[:,2]<165)
    assert not left.any() and not right.any()
    arm=raw('leftArm')
    assert ((arm[:,0]<-185)&(arm[:,1]<100)&(arm[:,2]>151)).any()
    assert sum(len(mesh.faces) for mesh in scene.geometry.values())==rig['triangles']==132061
