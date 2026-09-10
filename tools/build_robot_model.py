#!/usr/bin/env python3
"""Build the articulated GLB from the supplied assembly, never modifying it.

Requires trimesh, networkx and fast-simplification. Segmentation uses connected
component bounds in this specific source assembly, not arbitrary triangle cuts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import trimesh


def build(source, destination):
    mesh = trimesh.load(source)
    components = mesh.split(only_watertight=False)
    center_x = -103.086
    scale = .32 / 291.45
    def point(p):
        return np.array([(p[0]-center_x)*scale, (p[2]+52.595539)*scale, (p[1]-168)*scale])
    raw_pivots = {'body': (center_x,168,-52.595539),
                  'neck': (center_x,165,172), 'headLeftRight': (center_x,165,231),
                  'eyeLeft': (-137,165,255), 'eyeRight': (-69,165,255),
                  'leftArm': (-178,199,132), 'rightArm': (-28,199,132)}
    parents = {'body': 'world', 'neck':'body', 'headLeftRight':'neck',
               'eyeLeft':'headLeftRight','eyeRight':'headLeftRight',
               'leftArm':'body','rightArm':'body'}
    neutral = dict(rightArm=15, leftArm=170, neck=60, headLeftRight=90, eyeLeft=30, eyeRight=150)
    limits = dict(rightArm=[10,72],leftArm=[109,180],neck=[0,120],headLeftRight=[0,180],eyeLeft=[0,40],eyeRight=[140,170])
    axes = dict(rightArm=[1,0,0],leftArm=[1,0,0],neck=[1,0,0],headLeftRight=[0,1,0],eyeLeft=[0,0,1],eyeRight=[0,0,1])
    wheel_specs = {}
    for side, x in [('Left', -218), ('Right', 12)]:
        for index, (y, z, radius) in enumerate([(118.4, -26.3, 21), (218, -17.6, 34), (199.4, 54.9, 14), (132.7, 10.7, 14)]):
            name = f'wheel{side}{index}'
            raw_pivots[name] = (x, y, z)
            parents[name] = 'body'
            wheel_specs[name] = {'side': side.lower(), 'radius_m': radius * scale}
    groups = {name: [] for name in parents}
    for part in components:
        lo,hi=part.bounds; x,y,z=part.centroid
        if lo[2] > 233 and hi[0] < -115:
            name='eyeLeft'
        elif lo[2] > 233 and lo[0] > -100:
            name='eyeRight'
        elif lo[2] > 220:
            name='headLeftRight'
        elif lo[2] > 155 and lo[0] > -125 and hi[0] < -80:
            name='neck'
        elif lo[2] > 88 and hi[2] < 151 and hi[0] < -130:
            name='leftArm'
        elif lo[2] > 88 and hi[2] < 151 and lo[0] > -76:
            name='rightArm'
        else:
            name='body'
        # Eye shells extend below their lenses; assign their complete shells.
        if lo[2] > 220 and hi[2] > 280:
            name='eyeLeft' if x < center_x else 'eyeRight'
        if hi[2] < 82 and (hi[0] < -185 or lo[0] > -20) and hi[0]-lo[0] < 16:
            # Circular roller components only; the continuous track stays fixed.
            for wheel in wheel_specs:
                wx, wy, wz = raw_pivots[wheel]
                radius = wheel_specs[wheel]['radius_m'] / scale
                if (x < center_x) == (wx < center_x) and abs(y-wy) < 3 and abs(z-wz) < 3 and max(hi[1]-lo[1],hi[2]-lo[2]) < radius*2+3:
                    name = wheel
                    break
        color = [195,147,35,255] if name in ('body','leftArm','rightArm') else [112,119,126,255]
        if hi[2] < 82: color=[48,52,57,255]
        if name.startswith('eye') and y < 123 and hi[2]-lo[2] < 45: color=[25,40,48,255]
        vertices=np.array([point(v) for v in part.vertices])-point(raw_pivots[name])
        part=trimesh.Trimesh(vertices=vertices,faces=part.faces[:, ::-1],process=False)
        # Swapping source Y/Z reflects handedness: reverse triangle winding.
        if len(part.faces)>300:
            part=part.simplify_quadric_decimation(face_count=max(150,int(len(part.faces)*.28)))
        part.visual.vertex_colors=color
        groups[name].append(part)
    scene=trimesh.Scene(base_frame='world'); rig={'schema_version':1,'coordinate_system':'glTF Y-up metres',
        'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_name':source.name,
        'calibrated':False,'scale_m_per_source_unit':scale,'joints':{},'height_m':float(mesh.extents[2]*scale)}
    for name,parent in parents.items():
        origin=point(raw_pivots[name]); parent_origin=point(raw_pivots[parent]) if parent in raw_pivots else np.zeros(3)
        matrix=np.eye(4); matrix[:3,3]=origin-parent_origin
        scene.graph.update(frame_to=name,frame_from=parent,matrix=matrix)
        geometry=trimesh.util.concatenate(groups[name])
        scene.add_geometry(geometry,node_name=name+'_mesh',geom_name=name+'_geometry',parent_node_name=name)
        if name in neutral:
            rig['joints'][name]=dict(parent=parent,pivot_m=(origin-parent_origin).tolist(),axis=axes[name],
                neutral_deg=neutral[name],assembly_deg=neutral[name],limits_deg=limits[name],
                multiplier=-1 if name=='leftArm' else .35 if name=='neck' else 1)
    destination.mkdir(parents=True,exist_ok=True)
    def materials(tree):
        tree['materials'] = [{'name': 'painted-metal', 'pbrMetallicRoughness':
            {'baseColorFactor': [1,1,1,1], 'metallicFactor': .1, 'roughnessFactor': .8}}]
        for item in tree['meshes']:
            for primitive in item['primitives']:
                primitive['material'] = 0
    scene.export(destination/'robot.glb', tree_postprocessor=materials)
    rig['wheels'] = wheel_specs
    rig['triangles']=sum(len(g.faces) for g in scene.geometry.values())
    (destination/'rig.json').write_text(json.dumps(rig,indent=2)+'\n')
    print(json.dumps({'triangles':rig['triangles'],'bytes':(destination/'robot.glb').stat().st_size,'groups':{k:len(v) for k,v in groups.items()}}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    args=parser.parse_args();build(args.source,args.destination)
