"""One directed floor obstacle: blocked straight corridor with an A* detour."""
import itertools
import numpy as np

from .base import ModeBuilder
from .geometry import asset_candidates, lattice_points, navigation, role_profiles, shift_repair
from ..build.proposal import BuildProposal, ProbeResult
from ...planning.navigation.astar import search, segment_free


def corridor_pairs(geometry):
    rows = lattice_points(geometry, navigable=True)
    anchor = np.asarray(geometry.task['anchor_world'][:2])
    rows.sort(key=lambda r: np.linalg.norm(np.asarray(r['xy']) - anchor))
    goals, starts = rows[:5], rows[-8:]
    return [(a, b) for a, b in itertools.product(starts, goals)
            if np.linalg.norm(np.asarray(a['xy']) - b['xy']) >= geometry.navigation.resolution * 2]


class GroundObstacleBuilder(ModeBuilder):
    def candidates(self, ctx, rng):
        pairs = corridor_pairs(ctx.geometry)
        if not pairs:
            self.exhausted_reason = 'no_navigable_station_pair'
            return
        for start, goal in pairs[:8]:
            yield self._proposal(start, goal)
        profiles = role_profiles(ctx, 'floor_obstacle', rng, floor=True)
        if not profiles:
            self.exhausted_reason = 'asset_pool_empty:floor_obstacle'
            return
        for index in rng.permutation(len(pairs)):
            start, goal = pairs[int(index)]
            centers = [(1 - f) * np.asarray(start['xy']) + f * np.asarray(goal['xy']) for f in (.5, .35, .65)]
            for edit in asset_candidates(ctx, profiles, centers, floor=True):
                yield self._proposal(start, goal, [edit])

    def _proposal(self, start, goal, edits=()):
        return BuildProposal('corridor-' + start['station_id'] + '-' + goal['station_id'], 'ground_obstacle',
            [start['station_id'], goal['station_id']], edits=list(edits),
            metadata={'s0_hint': start['station_id'], 's1_hint': goal['station_id'],
                      'corridor_xy': [start['xy'], goal['xy']]})

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        geometry = ctx.geometry if geometry is None else geometry
        rows = {r['station_id']: r for r in (geometry.lattice if stations is None else stations)}
        ids = proposal.station_hints
        if any(identity not in rows for identity in ids):
            return ProbeResult('fail', 'corridor_hint_initialization_unavailable')
        start, goal = (rows[identity]['xy'] for identity in ids)
        grid, _ = navigation(ctx, proposal, sim=sim, geometry=geometry)
        if segment_free(grid, start, goal):
            return ProbeResult('fail', 'straight_corridor_not_blocked')
        path = search(grid, start, goal)
        direct = float(np.linalg.norm(np.asarray(start) - goal))
        ratio = path['length_m'] / direct if path and direct > 0 else None
        valid = ratio is not None and ratio >= ctx.settings.construction_detour_min
        return ProbeResult('pass' if valid else 'fail', 'blocked_with_detour' if valid else 'no_sufficient_detour',
                           {'direct_m': direct, 'path_m': path['length_m'] if path else None, 'detour_ratio': ratio})

    def repair(self, ctx, proposal, checks):
        a, b = np.asarray(proposal.metadata['corridor_xy'])
        direction = b - a
        return shift_repair(ctx, proposal, [-direction[1], direction[0]])
