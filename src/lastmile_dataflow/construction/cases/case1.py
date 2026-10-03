"""Case 1 (distance difficulty) shared-layer constructor.

Difficulty is a *distance* condition measured from the robot's frozen initial base, never from the
robot's current pose: a build session may legitimately contain earlier configured edits, and a
distance re-derived from a moved robot would silently answer a different question. The frozen base
travels in the session (`initial_robot_base`), so the condition is reproducible after any rollback.

Candidate generation walks the two support-local lines through the target's current position
(design 2.1: local line sampling, not a full-region grid) and keeps the target's own attitude,
changing world yaw only by the configured increments.
"""
import numpy as np

from .base import (CaseConstructor, Frame, INTENT, MEASUREMENT, PHYSICAL, SCENE, make_requirement,
                    world_to_local)
from ..generate import support_move_candidates


class Case1(CaseConstructor):
    """One reference frame (the support plane); the only case-specific judgement is distance."""

    case_type = 'case1'

    def build_frames(self, sim):
        return {self.restore_region.region_id: Frame.of_support_region(self.restore_region)}

    def preflight(self, sim):
        return []

    def distance(self, region, xy):
        world = np.asarray(region.origin) + np.asarray(region.axes) @ [xy[0], xy[1], 0.]
        return float(np.linalg.norm(world[:2] - self.frozen_initial_base[:2]))

    def local_check(self, sim, frames, requirements):
        config, target = self.config, self.config.target
        base = self.frozen_initial_base
        frame = frames[self.restore_region.region_id]
        position = sim.data.xpos[sim.model.body(target).id]
        local = world_to_local(frame.rotation, frame.origin, position)
        bounds = config.parameters['distance_range_m']
        distance = float(np.linalg.norm(position[:2] - base[:2]))
        evidence = {'target': target, 'frame_id': frame.frame_id,
                    'target_local_in_frame_xy': local[:2].tolist(),
                    'frozen_initial_base_xy': base[:2].tolist(),
                    'distance_m': distance, 'range_m': bounds,
                    'measurement_source': 'session.initial_robot_base_frozen_at_initialization',
                    'live_base_xy': sim.robot.group('base')[:2].tolist()}
        records = [make_requirement('target_robot_horizontal_distance',
                                    bool(bounds[0] <= distance <= bounds[1]), evidence,
                                    layer=INTENT, strength=MEASUREMENT, failure_kind='intent_failure',
                                    reason='frozen_initial_base_not_in_distance_range')]
        # Required intent rule (design 5.B, case 1): substituting the live robot pose for the frozen
        # one is a measurement-source inconsistency, not a quiet re-measurement.
        drift = float(np.linalg.norm(base[:2] - sim.robot.group('base')[:2]))
        records.append(make_requirement(
            'distance_measured_from_frozen_initial_base', drift <= 1e-6,
            {'frozen_xy': base[:2].tolist(), 'live_xy': sim.robot.group('base')[:2].tolist(),
             'delta_m': drift, 'rule': 'distance derives from the frozen initialization base'},
            layer=SCENE, strength=PHYSICAL, failure_kind='engineering_failure',
            reason='measurement_source_inconsistent'))
        return records

    def generate(self, sim, frames):
        config = self.config
        if config.target not in config.editable or 'move' not in config.allowed_operations: return []
        region = self.restore_region
        bounds = config.parameters['distance_range_m']
        yaws = list(config.parameters.get('yaw_candidates_rad') or [0.])

        def expected(pose, xy, axis):
            distance = self.distance(region, xy)
            return {'violations': int(not (bounds[0] <= distance <= bounds[1])),
                    'distance_m': distance, 'range_m': bounds,
                    'target_local_xy': [round(float(x), 6) for x in xy],
                    'frozen_initial_base_xy': self.frozen_initial_base[:2].tolist(),
                    'distance_measurement_source': 'session.initial_robot_base_frozen_at_initialization'}

        return support_move_candidates(sim, self.session, expected=expected, yaw_candidates=yaws,
                                       region_id=region.region_id)

    def pending_hypotheses(self):
        return ['current_station_failure', 'alternative_station_success']
