"""Directed blockers of nominal arm channels; no solved-path or IK claim."""
import itertools
import numpy as np

from .base import ModeBuilder
from .geometry import (asset_candidates, lattice_points, role_profiles, proposal_boxes,
                       arm_channels, channel_blocked, segment_boxes, shift_repair, nominal)
from ..build.proposal import BuildProposal, ProbeResult


class ArmPathCollisionBuilder(ModeBuilder):
    mode = 'arm_path_collision'
    role = 'arm_obstacle'
    maximum = 3
    approach = False

    def candidates(self, ctx, rng):
        rows = []
        for candidate in lattice_points(ctx.geometry):
            if nominal(ctx, candidate)['geometry'] == 'valid':
                rows.append(candidate)
            if len(rows) == 6:
                break
        if len(rows) < 2:
            self.exhausted_reason = 'no_station_pair_for_nominal_channels'
            return
        pairs = [(a, b) for a in rows for b in rows if a['station_id'] != b['station_id']]
        for start, goal in pairs[:8]:
            yield self._proposal(start, goal)
        floor = not ctx.geometry.supports
        profiles = role_profiles(ctx, self.role, rng, floor=floor)
        if not profiles:
            self.exhausted_reason = 'asset_pool_empty:' + self.role
            return
        for start, goal in pairs:
            channels = arm_channels(ctx, start, approach=self.approach)
            centers = [a + f * (b - a) for channel in channels
                       for a, b in zip(channel['points'], channel['points'][1:]) for f in (.4, .7)]
            pool = list(itertools.islice(asset_candidates(ctx, profiles, centers, floor=floor), ctx.settings.max_proposals))
            for first in pool:
                edits = [first]
                for count in range(1, self.maximum + 1):
                    proposal = self._proposal(start, goal, edits)
                    yield proposal
                    if self.cheap_validate(ctx, proposal).status == 'pass' or count == self.maximum:
                        break
                    unblocked = [channel for channel in channels if not channel_blocked(channel, proposal_boxes(ctx, proposal))]
                    scored = []
                    for edit in pool:
                        bounds = np.array([edit.placement['world_bounds']['min'], edit.placement['world_bounds']['max']])
                        overlapping = any(np.all(bounds[0] < np.asarray(e.placement['world_bounds']['max']))
                                          and np.all(np.asarray(e.placement['world_bounds']['min']) < bounds[1]) for e in edits)
                        if overlapping:
                            continue
                        gain = sum(channel_blocked(channel, bounds[None, :]) for channel in unblocked)
                        if gain:
                            scored.append((gain, edit))
                    if not scored:
                        break
                    edits = edits + [max(scored, key=lambda item: item[0])[1]]

    def _proposal(self, start, goal, edits=()):
        return BuildProposal(self.mode + '-' + start['station_id'] + '-' + goal['station_id'], self.mode,
            [start['station_id'], goal['station_id']], edits=list(edits),
            metadata={'channel_type': 'pregrasp_to_grasp' if self.approach else 'nominal_arm_capsules',
                      'scope': 'geometric_proxy_not_reference_successful_trajectory'})

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        geometry = ctx.geometry if geometry is None else geometry
        rows = {r['station_id']: r for r in (geometry.lattice if stations is None else stations)}
        if any(identity not in rows for identity in proposal.station_hints):
            return ProbeResult('fail', 'channel_hint_initialization_unavailable')
        start, goal = [rows[identity] for identity in proposal.station_hints]
        a = arm_channels(ctx, start, sim=sim, geometry=geometry, approach=self.approach)
        b = arm_channels(ctx, goal, sim=sim, geometry=geometry, approach=self.approach)
        if not a or not b:
            return ProbeResult('unknown', 'nominal_channels_unavailable')
        boxes = proposal_boxes(ctx, proposal, sim=sim, geometry=geometry)
        blocked = [channel_blocked(channel, boxes) for channel in a]
        alternate = any(not channel_blocked(channel, boxes) for channel in b)
        goal_clear = self.approach or any(not segment_boxes(channel['points'][-1], channel['points'][-1], boxes,
                                        channel['goal_radius']).any() for channel in a)
        valid = all(blocked) and alternate and goal_clear
        return ProbeResult('pass' if valid else 'fail', 'restricted_channel_with_alternative' if valid else 'no_channel_contrast',
            {'blocked_channels': sum(blocked), 'tested_channels': len(a), 'alternative_clear': alternate,
             'goal_neighborhood_clear': bool(goal_clear), 'scope': 'capsule_geometry_only_no_ik_or_trajopt'})

    def repair(self, ctx, proposal, checks):
        if not proposal.edits:
            return None
        position = np.asarray(proposal.edits[0].placement['point_xy'])
        return shift_repair(ctx, proposal, position - ctx.task['anchor_world'][:2])
