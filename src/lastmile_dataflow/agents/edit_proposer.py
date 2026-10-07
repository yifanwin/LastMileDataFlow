"""Agent3: scene-bound, multi-strategy symbolic suggestions, without edit ACLs."""
from .case_gateway import CONDITIONS, DSL_HELP
from .prompts import PROPOSER
from .contracts import validate_information
from ..scenes.local_context import agent_context
from ..construction.case_schema import require, text
from ..construction.dsl import SymbolicDSL
import numpy as np


SYSTEM = PROPOSER


def distance_context(graph):
    """Cheap measured geometry hints, not reachability or visibility proof."""
    station = graph.nodes.get('station_start', {}).get('pose', {}).get('position')
    if station is None:
        return {}
    station = np.asarray(station)[:2]
    objects, regions = {}, {}
    for name, node in graph.nodes.items():
        if node.get('kind') == 'object' and node.get('pose'):
            objects[name] = float(np.linalg.norm(np.asarray(node['pose']['position'])[:2]-station))
        if node.get('kind') == 'region':
            a, b, c, d = node['bounds']
            local = np.array([[a,c,0], [a,d,0], [b,c,0], [b,d,0]])
            world = np.asarray(node['origin']) + local @ np.asarray(node['axes']).T
            regions[name] = {'local_corners_xy': local[:, :2].tolist(),
                'world_corners_xyz': world.tolist(),
                'corner_distance_xy_to_station_m': np.linalg.norm(world[:, :2]-station, axis=1).tolist()}
    return {'scope': 'geometry_only_not_feasible_placement_certificate',
            'object_baseline_distance_xy_m': objects, 'region_corners': regions}


def propose_edits(template, graph, gateway, *, count=4, feedback=None, operations=('move', 'rotate'), assets=None,
                  context=None, plan=None, observation=None, asset_categories=()):
    if context is not None:
        return _propose_observed(template, context, plan, observation, assets, gateway, count=count, feedback=feedback or (),
                                 asset_categories=asset_categories)
    def parse(value):
        if not isinstance(value, dict) or set(value) != {'proposals'} or not isinstance(value['proposals'], list) or len(value['proposals']) > count:
            raise ValueError('invalid proposals envelope')
        parsed = [SymbolicDSL.from_dict(p) for p in value['proposals']]
        if len({p.proposal_id for p in parsed}) != len(parsed):
            raise ValueError('duplicate proposal IDs')
        for p in parsed:
            for name in p.bindings.values():
                if name not in graph.nodes:
                    raise ValueError('binding absent from measured graph:' + name)
            if set(o['op'] for o in p.operations) - set(operations):
                raise ValueError('operation not implemented')
            if p.semantic_conflicts(template):
                raise ValueError('proposal conflicts with endpoint requirements')
        return parsed
    return gateway.call('proposer', SYSTEM, {'template': template.to_dict(), 'graph': graph.to_dict(),
        'distance_context': distance_context(graph) if any(r.type in ('station', 'robot_station') for r in template.roles.values()) else {},
        'max_proposals': count, 'operations': list(operations), 'assets': assets or [], 'feedback': feedback or []}, parse)


def _propose_observed(template, context, plan, observation, assets, gateway, *, count=3, feedback=(), asset_categories=()):
    def parse(value):
        require(isinstance(value, dict) and set(value) == {'decision', 'information_request', 'proposals'}, 'proposal', 'invalid envelope')
        require(value['decision'] in ('propose', 'request_information', 'no_proposal'), 'decision', 'unknown decision')
        require(isinstance(value['proposals'], list) and len(value['proposals']) <= count, 'proposals', 'too many proposals')
        if value['decision'] != 'propose':
            require(not value['proposals'], 'proposals', 'information request cannot execute proposals')
            if value['decision'] == 'request_information':
                information = validate_information(value['information_request'], context)
                return value['decision'], [], information
            require(value['information_request'] is None, 'information_request', 'no_proposal uses null')
            return value['decision'], [], None
        require(value['information_request'] is None, 'information_request', 'propose uses null')
        require(bool(value['proposals']), 'proposals', 'propose requires at least one proposal')
        proposals = [SymbolicDSL.from_dict(p) for p in value['proposals']]
        require(len({p.proposal_id for p in proposals}) == len(proposals), 'proposals', 'duplicate IDs')
        for p in proposals:
            require(all(n in context.graph.nodes for n in p.bindings.values()), 'bindings', 'node absent from local context')
            require(p.bindings.get('target') == context.target, 'target', 'target differs from observation baseline')
            require(not p.semantic_conflicts(template), 'proposal', 'template conflict')
        return value['decision'], proposals, None
    return gateway.call('proposer', PROPOSER, {'template': template.to_dict(), 'context': agent_context(context),
        'plan': plan.to_dict(), 'observation': observation, 'assets': assets, 'available_asset_categories': list(asset_categories), 'max_proposals': count,
        'dsl_contract': DSL_HELP, 'conditions': CONDITIONS, 'feedback': list(feedback)}, parse,
        images=observation.get('images', [observation['image']]))
