"""Apply proposals with the existing construction session and physical checks."""
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

from ...runtime.construction.session import matched_control
from ..augmentation.checks import collision_pairs, load_support, matched_source_effects
from ..geometry import descendants
from ..loading.asset_profiles import canonical_category


@dataclass(frozen=True)
class PhysicalLimits:
    collision_tolerance_m: float = .001
    support_contact_tolerance_m: float = .008
    source_effect_tolerance_m: float = .006
    source_effect_rotation_rad: float = .04
    max_tilt_rad: float = .10


EDIT_ROLES = {
    'reachability': (None, 0), 'orientation': (None, 0), 'articulated_interaction': (None, 0),
    'ground_obstacle': ('floor_obstacle', 1), 'narrow_space': ('floor_obstacle', 2),
    'visibility': ('visual_occluder', 1), 'arm_path_collision': ('arm_obstacle', 3),
    'approach': ('approach_obstacle', 1),
}


def _check_edit_intent(ctx, proposal, changes):
    role, limit = EDIT_ROLES[proposal.proposal_intent]
    placements, moves = changes['placements'], changes['target_moves']
    if len(placements) > limit:
        raise ValueError('proposal_object_count_exceeds_mode_limit')
    profiles = {p.asset_id: p for p in ctx.selector.profiles}
    for placement in placements:
        profile = profiles[placement['asset_id']]
        if role not in profile.roles or canonical_category(profile.category) == canonical_category(ctx.task['category']):
            raise ValueError('proposal_asset_role_or_target_category_mismatch')
        if placement['kind'] == 'ground' and 'floor' not in profile.compatible_supports:
            raise ValueError('proposal_asset_not_floor_compatible')
        if placement['kind'] == 'surface':
            if not any(region.geom == placement['support_geom'] for region in ctx.geometry.supports):
                raise ValueError('proposal_support_not_in_task_context')
    if moves:
        allowed = {surface.geom for surface in ctx.geometry.supports}
        if (proposal.proposal_intent != 'reachability' or len(moves) != 1
                or moves[0]['body'] != ctx.task['target_body'] or moves[0]['support_geom'] not in allowed):
            raise ValueError('target_move_requires_same_target_and_support')


@contextmanager
def proposal_scene(ctx, proposal, *, deadline=None):
    """Yield physical evidence; final proxy/station checks precede any freeze."""
    changes = proposal.runtime_changes()
    _check_edit_intent(ctx, proposal, changes)
    placements, moves = changes['placements'], changes['target_moves']
    if not placements and not moves:
        yield ctx.baseline, ctx.geometry, {'valid': True, 'path': 'base_pose' if proposal.pose_edit else 'no_edit',
                                          'task_completion': 'unknown'}
        return
    limits = PhysicalLimits()
    physical = placements + moves
    names = [f'procedural_obstacle_{i}' for i in range(len(placements))] + [move['body'] for move in moves]
    sim = ctx.session.candidate(placements, target_moves=moves, deadline=deadline)
    try:
        collision = collision_pairs(sim, physical, limits, body_names=names)
        if not collision['valid']:
            yield sim, None, {'valid': False, 'reason': 'initial_edit_collision', 'collision': collision}
            return
        settling = ctx.session.settle(deadline=deadline, moved_bodies=[move['body'] for move in moves])
        control, _ = matched_control(ctx.baseline, settling['simulated_s'], ctx.settle_config, deadline)
        try:
            excluded = {sim.model.body(b).name for move in moves
                        for b in descendants(sim.model, sim.model.body(move['body']).id)}
            collision = collision_pairs(sim, physical, limits, settled=True, body_names=names)
            support = load_support(sim, physical, limits, body_names=names)
            effects = matched_source_effects(control, sim, limits, edited_bodies=excluded)
            healthy = bool(np.isfinite(sim.state_vector()).all() and not settling['new_warnings'])
            checks = {'valid': bool(settling['stable'] and healthy and collision['valid'] and support['valid'] and effects['valid']),
                      'settling': settling, 'solver_health': healthy, 'collision': collision, 'support': support,
                      'source_effects': effects, 'edited_source_bodies': sorted(excluded), 'task_completion': 'unknown'}
            geometry = ctx.derive(sim) if checks['valid'] else None
            yield sim, geometry, checks
        finally:
            control.close()
    finally:
        ctx.session.reject()
