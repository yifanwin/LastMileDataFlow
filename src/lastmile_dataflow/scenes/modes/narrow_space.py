"""Natural chassis clearance or one/two floor obstacles with recovery space."""
import numpy as np

from .base import ModeBuilder
from .geometry import asset_candidates, lattice_points, navigation, role_profiles, shift_repair
from ..build.proposal import BuildProposal, ProbeResult
from ...planning.astar import search, segment_free


class NarrowSpaceBuilder(ModeBuilder):
    def candidates(self, ctx, rng):
        rows = lattice_points(ctx.geometry, navigable=True)
        if len(rows) < 2:
            self.exhausted_reason = 'no_navigable_station_pair'
            return
        natural = BuildProposal('natural-narrow', 'narrow_space')
        yield natural
        profiles = role_profiles(ctx, 'floor_obstacle', rng, floor=True)
        if not profiles:
            self.exhausted_reason = 'asset_pool_empty:floor_obstacle'
            return
        grid = ctx.geometry.navigation
        rows.sort(key=lambda r: ctx.geometry.clearance_base[grid.cell(r['xy'])])
        chassis = ctx.geometry.chassis['radius_m']
        nav = ctx.geometry.navigation_footprint['radius_m'] + ctx.station_config.navigation_margin_m
        desired = (max(0., nav - chassis) + ctx.settings.clearance_low_m) / 2
        for row in rows[:8]:
            for profile in profiles:
                for direction in ((1., 0.), (-1., 0.), (0., 1.), (0., -1.)):
                    axis = np.asarray(direction)
                    half = (profile.width_m if axis[0] else profile.depth_m) / 2
                    centers = [np.asarray(row['xy']) + axis * (chassis + desired + half)]
                    for first in asset_candidates(ctx, [profile], centers, floor=True):
                        proposal = BuildProposal('narrow-' + row['station_id'], 'narrow_space',
                            [row['station_id']], edits=[first], metadata={'release_direction': (-axis).tolist()})
                        yield proposal
                        if self.cheap_validate(ctx, proposal).status != 'pass':
                            other = [np.asarray(row['xy']) - axis * (chassis + desired + half)]
                            for second in asset_candidates(ctx, [profile], other, floor=True):
                                yield BuildProposal(proposal.proposal_id + '-two', 'narrow_space', proposal.station_hints,
                                    edits=[first, second], metadata=proposal.metadata)

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        geometry = ctx.geometry if geometry is None else geometry
        rows = geometry.lattice if stations is None else stations
        grid, clearance = navigation(ctx, proposal, sim=sim, geometry=geometry)
        measured = [(r, grid.cell(r['xy'])) for r in rows]
        measured = [(r, cell) for r, cell in measured if cell is not None and grid.free[cell]]
        for start, cell in sorted(measured, key=lambda item: clearance[item[1]]):
            value = float(clearance[cell])
            if not 0 < value < ctx.settings.clearance_low_m:
                continue
            for goal, other in sorted(measured, key=lambda item: clearance[item[1]], reverse=True):
                gain = float(clearance[other] - value)
                if gain >= ctx.settings.clearance_gain_m and (segment_free(grid, start['xy'], goal['xy']) or search(grid, start['xy'], goal['xy'])):
                    proposal.station_hints = [start['station_id'], goal['station_id']]
                    return ProbeResult('pass', 'narrow_with_wider_reachable_pose',
                        {'s0_hint': start['station_id'], 's1_hint': goal['station_id'],
                         'clearance_base_m': value, 'clearance_gain_m': gain})
        return ProbeResult('fail', 'no_legal_narrow_and_wide_contrast')

    def repair(self, ctx, proposal, checks):
        return shift_repair(ctx, proposal, proposal.metadata.get('release_direction', [0., 0.]))
