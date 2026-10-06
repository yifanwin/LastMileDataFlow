"""Sequential symbolic placement compiler. Predicted layouts are NOT support proof."""
import copy
from dataclasses import dataclass, field
import itertools
import json
import time

import numpy as np
import mujoco

from .case_schema import CaseTemplate, required_checks_pass
from .dsl import ExecutableDSL, SymbolicDSL, relative_quaternion
from .sampling import sample_scalar, sample_xy, SamplingRNG
from ..scenes.graph import SceneGraph
from ..validation.predicates import _rotation, evaluate_conditions


class SearchExhausted(ValueError):
    """Finite candidate attempt failed; does not mean mathematical infeasibility."""


@dataclass
class CompilationBatch:
    candidates: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    attempted_count: int = 0


def resolve(ref, bindings, nodes):
    if ref.startswith('$'):
        name = ref[1:]
        if name not in bindings:
            raise SearchExhausted('unbound_role:' + name)
        ref = bindings[name]
    if ref not in nodes:
        raise SearchExhausted('missing_node:' + ref)
    return ref


def local_corners(node):
    if 'collision_bounds_local' not in node:
        raise SearchExhausted('collision_geometry_unknown')
    lo, hi = node['collision_bounds_local']
    return np.array(list(itertools.product(*zip(lo, hi))))


def update_layout(layout, name, pose):
    """Move local conservative geometry and attached static regions in order."""
    node = layout['nodes'][name]
    old = node['pose']
    r0, r1 = _rotation(old['quaternion_wxyz']), _rotation(pose['quaternion_wxyz'])
    p0, p1 = np.asarray(old['position']), np.asarray(pose['position'])
    delta = r1 @ r0.T
    points = local_corners(node) @ r1.T + p1
    node.update(pose=copy.deepcopy(pose), geometry_status='known',
                footprint_world=points.tolist(), bottom_height_m=float(points[:, 2].min()),
                collision_bounds_world=[points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                support_status='unknown', support_observations={})
    for region in layout['nodes'].values():
        if region.get('kind') == 'region' and region.get('support') == name:
            region['origin'] = (delta @ (np.asarray(region['origin'])-p0)+p1).tolist()
            region['axes'] = (delta @ np.asarray(region['axes'])).tolist()
            region['height'] = region['origin'][2]
            region['predicted'] = True
    layout['edges'] = [e for e in layout['edges'] if e['predicate'] != 'supported_by']


def placement(node, space, layout, bindings, rng, index, count, selection):
    nodes = layout['nodes']
    pose = copy.deepcopy(node['pose'])
    rotation = space.get('rotation')
    sampled = {}
    if rotation:
        angle = sample_scalar(rotation['angle'].get('range', rotation['angle'].get('value')), rng, index, count, selection)
        pose['quaternion_wxyz'] = relative_quaternion(pose['quaternion_wxyz'], rotation['axis'], angle, rotation.get('frame', 'world'))
        sampled['relative_angle_rad'] = angle
    rotated = local_corners(node) @ _rotation(pose['quaternion_wxyz']).T
    xy = space.get('xy', {'mode': 'uniform'})
    margin = xy.get('margin', .01)
    region_ref = space.get('region')
    radial = region_ref if isinstance(region_ref, dict) else None
    if isinstance(region_ref, str):
        regions = [nodes[resolve(region_ref, bindings, nodes)]]
    elif space.get('support'):
        support = resolve(space['support'], bindings, nodes)
        regions = [n for n in nodes.values() if n.get('kind') == 'region' and n.get('support') == support]
        if not regions and nodes[support].get('support_capability') == 'horizontal_plane':
            # Infinite plane placement requires explicit finite world x/y limits.
            if 'x' not in xy or 'y' not in xy:
                raise SearchExhausted('infinite_support_requires_bounded_xy')
            origin = list(nodes[support].get('support_plane_origin_world', nodes[support]['pose']['position']))
            origin[:2] = [0., 0.]
            regions = [{'region_id': None, 'origin': origin, 'axes': np.eye(3).tolist(),
                        'bounds': [*xy['x'], *xy['y']], 'height': origin[2], 'unbounded_plane': True}]
    else:
        regions = []
    region = None
    if regions:
        region = regions[int(rng.integers(len(regions)))]
        axes = np.asarray(region['axes'])
        if not np.allclose(axes[:, 2], [0, 0, 1], atol=1e-6):
            raise SearchExhausted('nonhorizontal_support_unsupported')
        footprint = rotated @ axes
        a, b, c, d = region['bounds']
        interval = [a+margin-footprint[:, 0].min(), b-margin-footprint[:, 0].max(),
                    c+margin-footprint[:, 1].min(), d-margin-footprint[:, 1].max()]
        if region.get('unbounded_plane'):
            interval = [*xy['x'], *xy['y']]
        for k, axis in enumerate(('x', 'y')):
            if axis in xy:
                interval[2*k] = max(interval[2*k], xy[axis][0])
                interval[2*k+1] = min(interval[2*k+1], xy[axis][1])
        if interval[0] > interval[1] or interval[2] > interval[3]:
            raise SearchExhausted('footprint_exceeds_region_or_xy_constraint')
        sampled_xy = sample_xy(interval, rng, mode=xy['mode'], index=index,
                               steps=xy.get('steps', 5), count=count, selection=selection)
        pos = np.asarray(region['origin']) + axes @ [*sampled_xy, 0]
        pos[2] = region['height'] - rotated[:, 2].min() + .003
    else:
        if space.get('frame') != 'world' or 'x' not in xy or 'y' not in xy:
            raise SearchExhausted('bounded_region_or_world_xy_required')
        sampled_xy = sample_xy([*xy['x'], *xy['y']], rng, mode=xy['mode'], index=index,
                               steps=xy.get('steps', 5), count=count, selection=selection)
        pos = np.array([*sampled_xy, pose['position'][2]])
    if radial:
        other = nodes[resolve(radial['relative_to'], bindings, nodes)]['pose']['position']
        distance = float(np.linalg.norm(pos[:2]-np.asarray(other)[:2]))
        if not radial['distance'][0] <= distance <= radial['distance'][1]:
            raise SearchExhausted('radial_search_condition_not_met')
    for k, axis in enumerate(('x', 'y', 'z')):
        if axis in space.get('offset', {}):
            pos[k] += sample_scalar(space['offset'][axis], rng, index, count, selection)
    pose['position'] = pos.tolist()
    sampled.update(xy=sampled_xy, position_world=pose['position'])
    return pose, region.get('region_id') if region else None, sampled


def compile_sample(proposal, template, graph, *, seed=42, index=0, sample_id=None, assets=None):
    proposal = proposal if isinstance(proposal, SymbolicDSL) else SymbolicDSL.from_dict(proposal)
    template = template if isinstance(template, CaseTemplate) else CaseTemplate.from_dict(template)
    conflicts = proposal.semantic_conflicts(template)
    if conflicts:
        raise SearchExhausted('semantic_conflict:' + conflicts[0].reason)
    layout = copy.deepcopy(graph.to_dict() if isinstance(graph, SceneGraph) else graph)
    layout['stage'] = 'observed'
    nodes = layout['nodes']
    bindings = copy.deepcopy(proposal.bindings)
    rng = SamplingRNG(seed, index)
    selection = proposal.sampling.get('selection', 'coverage')
    count = proposal.sampling.get('max_samples', 16)
    operations, sampled = [], []
    measured_edges = graph.edges if isinstance(graph, SceneGraph) else graph['edges']
    carry_edges = [copy.deepcopy(e) for e in measured_edges if e['predicate'] == 'supported_by']
    for op_index, op in enumerate(proposal.operations):
        if op['op'] == 'remove':
            name = resolve(op['subject'], bindings, nodes)
            if nodes[name].get('kind') != 'object' or not nodes[name].get('mjcf_body'):
                raise SearchExhausted('removable_scene_instance_required')
            operations.append({'op': 'remove', 'instance': name})
            removed = {name} | {k for k, n in nodes.items() if n.get('support') == name}
            # Model-tree parts disappear with their owning root.
            removed |= {e['args'][0] for e in layout['edges'] if e['predicate'] == 'part_of' and e['args'][1] == name}
            for key in removed:
                nodes.pop(key, None)
            layout['edges'] = [e for e in layout['edges'] if not set(e['args']) & removed]
            carry_edges = [e for e in carry_edges if not set(e['args']) & removed]
            sampled.append({'removed': name})
            continue
        if op['op'] == 'add':
            if assets is None:
                raise SearchExhausted('asset_catalog_unavailable')
            try:
                asset = assets.select(op['asset_selector'], rng)
            except ValueError as exc:
                raise SearchExhausted(str(exc)) from exc
            name = f'added_{seed}_{index}_{op_index}'
            while name in nodes:
                name += '_new'
            bindings[op['bind_as'][1:]] = name
            nodes[name] = {'kind': 'object', 'instance_id': name, 'mjcf_body': name,
                           'asset_id': asset['asset_id'], 'category': asset.get('category'),
                           'root_motion': asset['root_motion'], 'pose': copy.deepcopy(asset['pose']),
                           'collision_bounds_local': copy.deepcopy(asset['collision_bounds_local']),
                           'geometry_status': 'known', 'directions': {}, 'support_status': 'unknown'}
            for region in asset['regions']:
                region = copy.deepcopy(region)
                region.update(support=name, geom=name+'/'+region['geom'], kind='region')
                region['region_id'] = name+':'+region['geom']+':top'
                nodes[region['region_id']] = region
            pose, region_id, params = placement(nodes[name], op['search_space'], layout, bindings,
                                              rng, index, count, selection)
            operations.append({'op': 'add', 'instance': name, 'asset_id': asset['asset_id'], 'pose': pose})
            if region_id:
                operations[-1]['region_id'] = region_id
            sampled.append({**params, 'asset_id': asset['asset_id'], 'new_role': op['bind_as'][1:]})
            update_layout(layout, name, pose)
            continue
        name = resolve(op['subject'], bindings, nodes)
        node = nodes[name]
        if node.get('kind') != 'object':
            raise SearchExhausted('scene_object_required')
        if op['op'] == 'move':
            pose, region_id, params = placement(node, op['search_space'], layout, bindings, rng, index, count, selection)
        else:
            angle = sample_scalar(op['angle'].get('range', op['angle'].get('value')), rng, index, count, selection)
            pose = copy.deepcopy(node['pose'])
            pose['quaternion_wxyz'] = relative_quaternion(pose['quaternion_wxyz'], op['axis'], angle, op.get('frame', 'world'))
            region_id, params = None, {'relative_angle_rad': angle}
        operations.append({'op': op['op'], 'instance': name, 'pose': pose})
        if region_id:
            operations[-1]['region_id'] = region_id
        sampled.append(params)
        # Carry is expansion based on ACTUAL baseline support, not metadata parents.
        carry = []
        if op.get('carry_supported'):
            queue, seen = [name], {name}
            while queue:
                support_name = queue.pop(0)
                for edge in carry_edges:
                    if edge['predicate'] != 'supported_by' or edge['args'][1] != support_name:
                        continue
                    child = edge['args'][0]
                    if child in seen or child not in nodes:
                        continue
                    seen.add(child)
                    queue.append(child)
                    carry.append(child)
        if carry:
            old = copy.deepcopy(node['pose'])
            delta = _rotation(pose['quaternion_wxyz']) @ _rotation(old['quaternion_wxyz']).T
            for child in carry:
                child_pose = copy.deepcopy(nodes[child]['pose'])
                child_pose['position'] = (delta @ (np.asarray(child_pose['position'])-old['position'])+pose['position']).tolist()
                quaternion = np.zeros(4)
                mujoco.mju_mat2Quat(quaternion, (delta @ _rotation(child_pose['quaternion_wxyz'])).flatten())
                child_pose['quaternion_wxyz'] = quaternion.tolist()
                operations.append({'op': 'move', 'instance': child, 'pose': child_pose})
                update_layout(layout, child, child_pose)
                sampled.append({'carry_supported_by': name})
        carry_edges = [e for e in carry_edges if e['args'][0] != name and
                       (op.get('carry_supported') or e['args'][1] != name)]
        update_layout(layout, name, pose)
    # Only geometric numeric/region predicates filter the predicted layout.
    # True support is measured after actual execution and settling in S3.
    conditions = [c for c in proposal.effective_requirements(template) if c.predicate not in ('supported', 'supported_by')]
    checks = evaluate_conditions(conditions, layout, bindings=bindings, parameters=template.parameters)
    if not required_checks_pass(checks):
        failed = [{'item': c.item, 'value': c.value, 'reason': c.reason, 'evidence': c.evidence}
                  for c in checks if c.required and c.status != 'pass']
        raise SearchExhausted('joint_predicted_conditions_not_met:' + json.dumps(failed, ensure_ascii=False)[:2000])
    return ExecutableDSL(proposal.proposal_id, sample_id or f'sample_{index:04d}', operations,
                         {'seed': seed, 'index': index, 'bindings': bindings, 'operations': sampled,
                          'parameter_sources': {k: v.to_dict() for k, v in template.parameters.items()}})


def compile_candidates(proposal, template, graph, *, seed=42, max_samples=None, deadline=None, assets=None):
    proposal = proposal if isinstance(proposal, SymbolicDSL) else SymbolicDSL.from_dict(proposal)
    batch = CompilationBatch()
    for index in range(max_samples if max_samples is not None else proposal.sampling.get('max_samples', 16)):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('compile wall-clock budget exhausted')
        batch.attempted_count += 1
        try:
            batch.candidates.append(compile_sample(proposal, template, graph, seed=seed, index=index, assets=assets))
        except SearchExhausted as exc:
            batch.failures.append({'index': index, 'status': 'search_exhausted', 'reason': str(exc)})
    return batch
