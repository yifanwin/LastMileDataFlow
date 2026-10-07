"""Measured local task neighbourhoods; crop information, never physical obstacles."""
import copy
from dataclasses import dataclass
import numpy as np

from .graph import SceneGraph
from ..io import digest


class ContextUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class LocalContext:
    context_id: str
    target: str
    support: str
    radius_m: float
    graph: SceneGraph

    def work_regions(self):
        box = self.graph.nodes.get(self.support, {}).get('collision_bounds_world')
        if box is None:
            return []
        lo, hi = np.asarray(box)[:, :2]
        center = (lo+hi)/2
        regions = []
        for name, axis, sign in (('x_plus', 0, 1), ('x_minus', 0, -1), ('y_plus', 1, 1), ('y_minus', 1, -1)):
            point = center.copy()
            point[axis] = (hi[axis] if sign > 0 else lo[axis]) + sign*.7
            regions.append({'region_id': self.context_id + '/' + name,
                'center_xy_m': point.tolist(), 'world_direction': name,
                'scope': 'geometric_search_hint_not_standing_or_grasp_certificate'})
        return regions

    def to_dict(self):
        return {'context_id': self.context_id, 'target': self.target, 'support': self.support,
                'radius_m': self.radius_m, 'graph': self.graph.to_dict(),
                'work_regions': self.work_regions(), 'scope': 'agent_context_only_full_physics_preserved'}


def near_xy(node, center, radius):
    box = node.get('collision_bounds_world')
    if box is not None:
        lo, hi = np.asarray(box)[:, :2]
        return np.linalg.norm(np.maximum(np.maximum(lo-center, center-hi), 0)) <= radius
    if node.get('support_capability') == 'horizontal_plane':
        return True
    return bool(node.get('pose') and np.linalg.norm(np.asarray(node['pose']['position'])[:2]-center) <= radius)


def crop_context(graph, target, support, radius_m, *, max_nodes=240, context_id=None):
    center = np.asarray(graph.nodes[target]['pose']['position'])[:2]
    names = {target, support, 'station_start'}
    names |= {n for n, node in graph.nodes.items() if node.get('kind') == 'object' and near_xy(node, center, radius_m)}
    changed = True
    while changed:
        old = set(names)
        for edge in graph.edges:
            a, b = edge['args'][:2]
            if edge['predicate'] == 'supported_by' and a in names:
                names.add(b)
            if edge['predicate'] == 'part_of' and b in names:
                names.add(a)
            if edge['predicate'] == 'has_region' and a in names:
                names.add(b)
        # Carry closure for furniture, not flood-fill all objects supported by a floor.
        names |= {e['args'][0] for e in graph.edges if e['predicate'] == 'supported_by'
                  and e['args'][1] in names
                  and graph.nodes.get(e['args'][1], {}).get('support_capability') != 'horizontal_plane'
                  and not any(s in e['args'][1].lower() for s in ('floor', 'room_'))}
        changed = old != names
    names &= graph.nodes.keys()
    if len(names) > max_nodes:
        raise ContextUnavailable(f'local_context_too_large:{len(names)}>{max_nodes}')
    local = SceneGraph(graph.scene_id, graph.revision, graph.time_s,
        {n: copy.deepcopy(graph.nodes[n]) for n in sorted(names)},
        [copy.deepcopy(e) for e in graph.edges if all(n in names for n in e['args'])], graph.stage,
        [copy.deepcopy(i) for i in graph.issues if i.get('instance') in names])
    return LocalContext(context_id or 'context:' + target, target, support, radius_m, local)


def sample_contexts(graph, config, *, seed=0):
    supports = {e['args'][0]: e['args'][1] for e in graph.edges if e['predicate'] == 'supported_by'}
    anchors = [n for n, node in sorted(graph.nodes.items()) if node.get('kind') == 'object'
               and node.get('root_motion') == 'free' and 'move' in node.get('construction_capabilities', [])
               and n in supports and node.get('geometry_status') == 'known']
    # Sample supports before taking additional objects from the same support.
    # Otherwise a crowded counter can consume every slot and hide a dining table
    # from the strategy Agent. Neither ordering nor coverage certifies feasibility.
    rng = np.random.default_rng(seed)
    groups = {}
    for target in anchors:
        groups.setdefault(supports[target], []).append(target)
    keys = sorted(groups)
    support_order = [keys[int(i)] for i in rng.permutation(len(keys))]
    queues = {s: [groups[s][int(i)] for i in rng.permutation(len(groups[s]))] for s in keys}
    ordered = []
    while any(queues.values()):
        for support in support_order:
            if queues[support]:
                ordered.append(queues[support].pop())
    contexts, rejected = [], []
    for target in ordered:
        try:
            contexts.append(crop_context(graph, target, supports[target], config.radius_m, max_nodes=config.max_nodes))
        except ContextUnavailable as exc:
            rejected.append({'target': target, 'reason': str(exc)})
        if len(contexts) >= config.max_contexts:
            break
    return contexts, rejected


def summarize(context):
    return {'context_id': context.context_id, 'target': context.target, 'support': context.support,
        'radius_m': context.radius_m, 'node_count': len(context.graph.nodes), 'work_regions': context.work_regions(),
        'objects': {name: {k: node[k] for k in ('category', 'room_id', 'root_motion', 'pose',
            'collision_bounds_world', 'support_capability', 'construction_capabilities') if k in node}
            for name, node in context.graph.nodes.items() if node.get('kind') == 'object'},
        'supported_by': [e['args'] for e in context.graph.edges if e['predicate'] == 'supported_by']}


def agent_context(context):
    """Geometry projection, not a physics snapshot. Keep all IDs/region frames.

    Detailed support rays and mesh footprints remain in the on-disk full graph;
    repeating them twice in a review can consume hundreds of thousands of tokens.
    Full-precision geometry is still used by the compiler and rule program.
    """
    value = context.to_dict()
    graph = value['graph']
    original_id = graph.pop('graph_id')
    excluded = {'support_observations', 'footprint_world', 'collision_bounds_local', 'parent_hint'}
    graph['nodes'] = {name: {k: v for k, v in node.items() if k not in excluded}
                      for name, node in graph['nodes'].items()}
    graph['edges'] = [{k: e[k] for k in ('predicate', 'args') if k in e} for e in graph['edges']]
    def rounded(value):
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, dict):
            return {k: rounded(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rounded(v) for v in value]
        return value
    value = rounded(value)
    value['graph']['source_graph_id'] = original_id
    value['graph']['graph_id'] = digest(value['graph'])
    value['scope'] = 'agent_geometry_projection_full_physics_preserved_numeric_rounding_1e-6'
    return value
