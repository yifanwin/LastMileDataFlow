"""Three-camera occlusion intent, with a navigable better-view candidate."""
import numpy as np

from .base import ModeBuilder
from .geometry import asset_candidates, lattice_points, nominal, proxy_visibility, role_profiles, navigation, shift_repair
from ..build.proposal import BuildProposal, ProbeResult
from ...planning.navigation.astar import search, segment_free


class VisibilityBuilder(ModeBuilder):
    def candidates(self, ctx, rng):
        rows = lattice_points(ctx.geometry, navigable=True)
        views = []
        for candidate in rows:
            row = nominal(ctx, candidate)
            if row['geometry'] == 'valid':
                views.append(row)
            if len(views) == 8:
                break
        pairs = [(a, b) for a in views for b in views if a['station_id'] != b['station_id']]
        for start, goal in pairs[:8]:
            yield self._proposal(start, goal)
        if len(views) < 2:
            self.exhausted_reason = 'no_navigable_valid_view_pair'
            return
        floor = not ctx.geometry.supports
        height = float(np.ptp(ctx.geometry.target_points[:, 2])) + ctx.station_config.gripper_width_m / 2
        profiles = role_profiles(ctx, 'visual_occluder', rng, floor=floor, size_bounds={'height_m': (height, np.inf)})
        if not profiles:
            self.exhausted_reason = 'asset_pool_empty:visual_occluder'
            return
        anchor = np.asarray(ctx.task['anchor_world'])
        for start in views:
            cameras = start['visibility_proxy']['cameras']
            origins = [c['origin'] for c in cameras.values() if 'origin' in c]
            if not origins:
                continue
            origins = np.asarray(origins)
            centers = [anchor[:2] + f * (origin[:2] - anchor[:2])
                       for origin in [origins.mean(axis=0), *origins] for f in (.25, .5)]
            for edit in asset_candidates(ctx, profiles, centers, floor=floor):
                for goal in views:
                    if goal['station_id'] != start['station_id']:
                        proposal = self._proposal(start, goal, [edit])
                        proposal.metadata['ray_band_origins'] = origins.tolist()
                        yield proposal

    def _proposal(self, start, goal, edits=()):
        return BuildProposal('view-' + start['station_id'] + '-' + goal['station_id'], 'visibility',
            [start['station_id'], goal['station_id']], edits=list(edits),
            metadata={'s0_hint': start['station_id'], 's1_hint': goal['station_id']})

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        geometry = ctx.geometry if geometry is None else geometry
        rows = {r['station_id']: r for r in (geometry.lattice if stations is None else stations)}
        if any(identity not in rows for identity in proposal.station_hints):
            return ProbeResult('fail', 'visibility_hint_initialization_unavailable')
        start, goal = [rows[identity] for identity in proposal.station_hints]
        if stations is None:
            start = nominal(ctx, start, sim=sim, geometry=geometry)
            goal = nominal(ctx, goal, sim=sim, geometry=geometry)
            a = proxy_visibility(start, proposal.edits if sim is None else ())
            b = proxy_visibility(goal, proposal.edits if sim is None else ())
        else:
            a, b = start['visibility'], goal['visibility']
        if not a.get('complete') or b.get('known_max') is None:
            return ProbeResult('unknown', 'visibility_observation_incomplete')
        low, high = a['value'], b['known_max']
        good = low < ctx.settings.visibility_low and high > ctx.settings.visibility_good and high - low > ctx.settings.visibility_gain
        if not good:
            return ProbeResult('fail', 'no_aggregate_visibility_contrast', {'s0_visibility': low, 's1_visibility': high})
        grid, _ = navigation(ctx, proposal, sim=sim, geometry=geometry)
        path = segment_free(grid, start['xy'], goal['xy']) or search(grid, start['xy'], goal['xy'])
        return ProbeResult('pass' if path else 'fail', 'low_view_with_reachable_better_view' if path else 'better_view_not_reachable',
                           {'s0_visibility': low, 's1_visibility': high, 'gain': high - low})

    def repair(self, ctx, proposal, checks):
        if not proposal.edits:
            return None
        position = np.asarray(proposal.edits[0].placement['point_xy'])
        return shift_repair(ctx, proposal, np.asarray(ctx.task['anchor_world'][:2]) - position)
