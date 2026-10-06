"""Agent2: scene-bound, multi-strategy symbolic suggestions, without edit ACLs."""
from .case_gateway import COMMON, CONDITIONS, DSL_HELP
from ..construction.dsl import SymbolicDSL
import numpy as np


SYSTEM = COMMON + CONDITIONS + DSL_HELP + '''
Return {"proposals":[proposal,...]}. Respect requested maximum. Bind actual graph
object IDs, station_start and regions; do not guess IDs or asset categories. Prefer
diverse meaningful layouts, NOT minimal edits. Roles for additional objects are
allowed. Parent hints are not support proof. Unsupported articulated roots and
unknown complex support geometry cannot be treated as reliable placement surfaces.
construction_capabilities are measured PROGRAM editing capabilities. The separate
manipulable="unknown" field concerns ROBOT manipulation and does not prohibit ordinary
construction move/rotate. Do not require known robot graspability for a layout edit.
Use coverage/random sampling within each meaningful search space and distribute
strategies across feasible layouts. Large moves/relative angles, multiple objects and
furniture are welcome; no edit-size cost exists. Full-space exploration is a PROGRAM
generation policy, not a required visible property of each individual sample.
Region axes are matrix COLUMNS: world = origin + axes @ [local_x,local_y,0].
The program supplies distance_context with measured object distances and region
corner local/world coordinates; use it to avoid transposing axes or inventing distances.
Corners are not certified placements: still leave footprint/margin and check rules.
For a before/after distance increase, compute each candidate object's baseline XY
distance to station_start from actual graph positions. Choose destination regions
farther than THAT baseline, not an arbitrary generic final-distance range. You may
add an after distance goal whose minimum is baseline_distance plus a meaningful
margin. Use multiple different exposed object bindings, not only different supports
for one hidden object. Visual evidence must show the chosen objects BEFORE and AFTER the edit. Prefer
exposed supported objects; objects inside closed fridges/cabinets may be hidden even
when supported. Geometry is not visibility proof. After uncertain visibility feedback,
change the binding/strategy rather than repeat parameters on the same hidden object.
Local coordinate axes are geometric proxies, not automatically the visible long
axis or semantic front of an asset. A 90-degree proxy difference can still leave
two DIFFERENT assets visually parallel. If visual feedback rejects that discrepancy,
change the relative-angle strategy and prefer a broad angle RANGE rather than
repeating the same hardcoded quarter-turn. Rule predicates filter sampled angles;
Agent3 still checks actual visible orientation. Do not redefine template criteria.
Maintain required endpoint invariants; furniture can be moved before placing objects
on its NEW region. No generated Python, executable poses, control settings or permissions.
If roles cannot be bound or layout cannot express the intent, return proposals:[];
do not invent scene facts or upgrade pending hypotheses to proven difficulty.'''


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


def propose_edits(template, graph, gateway, *, count=4, feedback=None, operations=('move', 'rotate'), assets=None):
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
