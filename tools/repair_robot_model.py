#!/usr/bin/env python3
"""Correct distal hand attachments in an existing source-derived GLB.

This is a lossless face reassignment for deployments without the original STL.
The STL builder uses the same component predicate for future full rebuilds.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import trimesh

from build_robot_model import distal_arm_attachment


def repair(directory):
    path = directory / 'robot.glb'
    rig_path = directory / 'rig.json'
    rig = json.loads(rig_path.read_text())
    scene = trimesh.load_scene(path)
    # glTF's vertex colours arrive on TextureVisuals. Convert before submeshing;
    # concatenating texture visuals would otherwise drop the paint colours.
    for geometry in scene.geometry.values():
        if geometry.visual.kind == 'texture' and 'color' in geometry.visual.vertex_attributes:
            geometry.visual = trimesh.visual.ColorVisuals(geometry,
                vertex_colors=geometry.visual.vertex_attributes['color'])
    body = scene.geometry['body_geometry']
    transform, _ = scene.graph.get('body_mesh')
    scale = rig['scale_m_per_source_unit']
    groups = trimesh.graph.connected_components(body.face_adjacency,
        nodes=np.arange(len(body.faces)), min_len=1)
    removed = np.zeros(len(body.faces), dtype=bool)
    moved = {}
    for indices in groups:
        vertices = np.unique(body.faces[indices])
        world = trimesh.transform_points(body.vertices[vertices], transform)
        source = np.column_stack([world[:,0]/scale-103.086,
                                  world[:,2]/scale+168, world[:,1]/scale-52.595539])
        name = distal_arm_attachment(np.array([source.min(axis=0), source.max(axis=0)]))
        if name:
            moved.setdefault(name, []).append(indices)
            removed[indices] = True
    if not moved:
        print('All distal hand components are already attached to their arms.')
        return {}
    before = sum(len(mesh.faces) for mesh in scene.geometry.values())
    report = {}
    for name, groups in moved.items():
        indices = np.concatenate(groups)
        part = body.submesh([indices], append=True, repair=False)
        target, _ = scene.graph.get(name + '_mesh')
        part.apply_transform(np.linalg.inv(target) @ transform)
        scene.geometry[name + '_geometry'] = trimesh.util.concatenate([
            scene.geometry[name + '_geometry'], part])
        report[name] = dict(components=len(groups), triangles=len(indices))
    scene.geometry['body_geometry'] = body.submesh([np.flatnonzero(~removed)], append=True, repair=False)
    assert sum(len(mesh.faces) for mesh in scene.geometry.values()) == before
    destination = path.with_suffix('.tmp.glb')
    def materials(tree):
        tree['materials'] = [{'name':'painted-metal', 'pbrMetallicRoughness':
            {'baseColorFactor':[1,1,1,1], 'metallicFactor':.1, 'roughnessFactor':.8}}]
        for mesh in tree['meshes']:
            for primitive in mesh['primitives']:
                primitive['material'] = 0
    try:
        scene.export(destination, tree_postprocessor=materials)
        destination.replace(path)
    finally:
        destination.unlink(missing_ok=True)
    rig['articulation_revision'] = 2
    rig['articulation_repairs'] = report
    rig_path.write_text(json.dumps(rig, indent=2) + '\n')
    print(json.dumps(report))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    repair(parser.parse_args().directory)
