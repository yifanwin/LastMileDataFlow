"""Legacy case2/case3 checkers, kept behaviour-identical behind the shared CaseConstructor API.

These two cases still carry their historical rule thresholds (the design report marks them as
later work), so the only change here is annotation: each record now states which layer it belongs to
and how strong its evidence is, and the requirement *order* of case2/case3 is preserved exactly
because downstream tests read `requirements[0]`.
"""
import numpy as np

from .base import (CaseConstructor, Frame, INTENT, MEASUREMENT, PHYSICAL, SCENE, make_requirement)
from ...scenes.geometry import descendants


class _Legacy(CaseConstructor):
    def build_frames(self, sim):
        return {self.restore_region.region_id: Frame.of_support_region(self.restore_region)}

    def preflight(self, sim):
        return []

    def local_check(self, sim, frames, requirements):
        raise NotImplementedError


class LegacyCase2(_Legacy):
    """Handle orientation vs. required manipulation side; single-sided annotation is not enough."""

    case_type = 'case2'

    def local_check(self, sim, frames, requirements):
        p = self.parameters
        model, data = sim.model, sim.data
        target = model.body(self.config.target).id
        handle = p['handle']
        body = model.body(handle['body']).id
        if body not in descendants(model, target):
            raise ValueError('handle annotation is not a target part')
        actual = data.xmat[body].reshape(3, 3) @ np.asarray(handle['axis_local'])
        desired = np.asarray(p['desired_direction_world'], dtype=float)
        angle = float(np.arccos(np.clip(np.dot(actual, desired) / np.linalg.norm(actual) / np.linalg.norm(desired), -1, 1)))
        # The annotation source is part of the evidence: models must not fabricate a handle.
        single_sided = handle.get('source') is None or handle.get('verified') is not True
        return [make_requirement(
            'verified_handle_world_direction',
            bool(angle <= p.get('direction_tolerance_rad', .15) and not single_sided),
            {'angle_rad': angle, 'actual_world': actual.tolist(), 'annotation': handle,
             'annotation_is_single_sided': bool(single_sided)},
            layer=INTENT, strength=MEASUREMENT, failure_kind='intent_failure',
            reason='handle_direction_mismatch_or_unverified_annotation')]

    def pending_hypotheses(self):
        return ['real_handle_grasp_success']


class LegacyCase3(_Legacy):
    """Approach corridor obstruction; the obstacle must exist and must not be deleted by repair."""

    case_type = 'case3'

    def local_check(self, sim, frames, requirements):
        p = self.parameters
        model, data = sim.model, sim.data
        target = model.body(self.config.target).id
        try:
            obstacle = model.body(p['obstacle']).id
        except KeyError:
            return [make_requirement(
                'obstacle_approach_corridor', False, {'reason': 'required_obstacle_missing'},
                layer=SCENE, strength=PHYSICAL, failure_kind='engineering_failure',
                reason='required_obstacle_missing')]
        delta = data.xpos[obstacle] - data.xpos[target]
        expected = np.asarray(p['approach_offset_m'])
        tolerance = p['obstacle_distance_range_m']
        distance = float(np.linalg.norm(delta[:2]))
        inside = bool(tolerance[0] <= distance <= tolerance[1])
        # World-frame approach corridor as specified by the config; the local-frame expression is
        # added as evidence so the corridor's frame is explicit rather than implied.
        local = frames[self.restore_region.region_id].local(data.xpos[obstacle]) - \
            frames[self.restore_region.region_id].local(data.xpos[target])
        return [make_requirement(
            'obstacle_approach_corridor',
            bool(inside and np.linalg.norm(delta[:2] - expected[:2]) <= .08),
            {'distance_m': distance, 'delta_world_m': delta.tolist(), 'expected_world_m': expected.tolist(),
             'delta_support_local_m': local.tolist(), 'range_m': tolerance,
             'obstacle': p['obstacle'], 'protected_on_commit': True},
            layer=SCENE, strength=PHYSICAL, failure_kind='engineering_failure',
            reason='obstacle_corridor_geometry_failed')]

    def pending_hypotheses(self):
        return ['real_planning_path_obstructed', 'possible_bypass']
