"""Case 1.5 (lateral difference) shared-layer constructor.

Direction meaning is bound to the furniture's own local frame: a side vector is a *local* vector of
`frame_body`, and both the local and the world expression go into the evidence, so the orientation
cannot drift with a camera viewpoint.

Preflight is a three-part spatial check per side point: outside the furniture footprint, at a
plausible standing height, and over open floor. When a side vector points into the furniture, sits
at an absurd height, or has no floor under it, no candidate is generated at all — a layout whose
sides are not even physically placed has no case intent to edit.
"""
from types import SimpleNamespace

import mujoco
import numpy as np

from .base import CaseConstructor, Frame, INTENT, MEASUREMENT, PROXY, make_requirement
from ..generate import support_move_candidates
from ...robots.rby1 import RBY1Adapter
from ...scenes.geometry import (body_points, descendants, free_space_distance, geom_local_bounds,
                                obstacle_geoms)
from ...scenes.initialization import floor_support

CLEARANCE_METHOD = 'horizontal_ray_fan_at_base_height_not_navigation'


def standing_proxy(sim, robot_config, collection, base):
    """Read-only "could the robot stand here" query on scratch MjData.

    Read-only by construction: the build session's own MjData is copied, never stepped or moved, so
    a preflight check cannot perturb the revision it is measuring. A proxy that cannot run reports
    that honestly instead of silently passing.
    """
    scratch = mujoco.MjData(sim.model)
    mujoco.mj_copyData(scratch, sim.model, sim.data)
    try:
        robot = RBY1Adapter(sim.model, scratch, robot_config)
        for name, value in zip(robot.groups['base'], base):
            robot._check_joint(name, value)
            scratch.qpos[robot.addresses[name]] = value
        mujoco.mj_forward(sim.model, scratch)
        from ...stations.sampling import filter_station
        result = filter_station(SimpleNamespace(model=sim.model, data=scratch, robot=robot), collection)
        return {'available': True, 'method': 'geometry_filter_only_not_navigation',
                'status': result['status'], 'ground': result['ground'],
                'robot_collisions': result['robot_collisions'],
                'severe_issues': result['severe_issues']}
    except Exception as exc:
        return {'available': False, 'reason': f'{type(exc).__name__}:{exc}'}


class Case1_5(CaseConstructor):
    case_type = 'case1.5'

    def build_frames(self, sim):
        frame_body = self.parameters['frame_body']
        return {'frame_body': Frame.of_body(sim, sim.model.body(frame_body).id, frame_body,
                                            'furniture_local', 'configured_frame_body_compiled_pose'),
                self.restore_region.region_id: Frame.of_support_region(self.restore_region)}

    def side_names(self):
        return [key for key in ('side_a', 'side_b', 'side_c') if key in self.parameters]

    def roles(self):
        return dict(self.parameters.get('side_roles', {}))

    def sides(self, sim, frames):
        """Ordered (label, local vector, world point) triples for every configured side."""
        frame = frames['frame_body']
        rows = []
        for key in self.side_names():
            vector = np.asarray(self.parameters[key], dtype=float)
            rows.append((key, vector, frame.world(vector)))
        return rows

    def preflight(self, sim):
        frame_body = self.parameters['frame_body']
        furniture = sim.model.body(frame_body).id
        frame = Frame.of_body(sim, furniture, frame_body, 'furniture_local')
        footprint = frame.local(body_points(sim, furniture))[:, :2]
        bounds = [float(footprint[:, 0].min()), float(footprint[:, 0].max()),
                  float(footprint[:, 1].min()), float(footprint[:, 1].max())]
        ceiling = float(self.parameters.get('max_side_height_m', .05))
        records = []
        for key, vector, world_point in self.sides(sim, {'frame_body': frame}):
            local = frame.local(world_point)
            inside = bool(bounds[0] <= local[0] <= bounds[1] and bounds[2] <= local[1] <= bounds[3])
            records.append(make_requirement(
                f'side_point_outside_furniture_footprint::{key}', not inside,
                {'side': key, 'role': self.roles().get(key), 'frame_id': frame_body,
                 'side_vector_local': vector.tolist(), 'side_point_local': local.tolist(),
                 'furniture_footprint_local_xy': bounds, 'world_xy': world_point[:2].tolist()},
                layer=INTENT, strength=MEASUREMENT, failure_kind='intent_failure',
                reason='side_vector_points_inside_furniture'))
            records.append(make_requirement(
                f'side_point_at_standing_height::{key}', bool(-1e-6 <= world_point[2] <= ceiling),
                {'side': key, 'world_z': float(world_point[2]), 'max_side_height_m': ceiling},
                layer=INTENT, strength=MEASUREMENT, failure_kind='intent_failure',
                reason='side_point_not_at_standing_height'))
            records.append(make_requirement(
                f'side_point_over_free_space::{key}', bool(floor_support(sim, world_point[:2])),
                {'side': key, 'world_xy': world_point[:2].tolist(), 'probe_z': .20,
                 'method': 'named_floor_ray_plus_room_triangle_mask'},
                layer=INTENT, strength=MEASUREMENT, failure_kind='intent_failure',
                reason='side_point_not_over_open_floor'))
        return records

    def clearance(self, sim, point):
        """Free space around a standing position: a conservative geometric proxy, not navigation.

        Rays are cast at robot base heights, so tabletop assets never read as floor-level
        obstructions the way a bounding sphere would.
        """
        excluded = descendants(sim.model, sim.model.body(self.parameters['frame_body']).id) | \
            descendants(sim.model, sim.model.body(self.config.target).id)
        cache = getattr(self, '_free_space_cache', None)
        if cache is None:
            geoms = obstacle_geoms(sim, excluded)
            cache = self._free_space_cache = (geoms, {g: geom_local_bounds(sim, g) for g in geoms})
        geoms, bounds = cache
        return float(free_space_distance(sim, point, float(self.parameters['clearance_radius_m']),
                                         geoms=geoms, bounds=bounds))

    def local_check(self, sim, frames, requirements):
        parameters = self.parameters
        target = sim.data.xpos[sim.model.body(self.config.target).id]
        sides = self.sides(sim, frames)
        if len(sides) < 2: raise ValueError('case1.5 requires at least two side vectors')
        distances = [float(np.linalg.norm(p[:2] - target[:2])) for _, _, p in sides]
        clearances = [self.clearance(sim, p) for _, _, p in sides]
        records = [make_requirement(
            'frame_bound_side_distance_difference',
            distances[1] - distances[0] >= parameters['min_distance_difference_m'],
            {'frame_body': parameters['frame_body'], 'frame_id': 'frame_body',
             'side_labels': [k for k, _, _ in sides], 'side_roles': self.roles(),
             'side_vectors_local': [v.tolist() for _, v, _ in sides],
             'side_points_world': [p.tolist() for _, _, p in sides],
             'target_world_xy': target[:2].tolist(), 'distances_m': distances,
             'min_difference_m': parameters['min_distance_difference_m'],
             'convention': 'side vectors and the distance difference live in the furniture local frame'},
            layer=INTENT, strength=MEASUREMENT, failure_kind='intent_failure',
            reason='far_side_not_far_enough')]
        records.append(make_requirement(
            'frame_bound_side_clearance_difference',
            clearances[0] - clearances[1] >= parameters['min_clearance_difference_m'],
            {'clearances_m': clearances, 'min_difference_m': parameters['min_clearance_difference_m'],
             'method': CLEARANCE_METHOD,
             'clearance_radius_m': parameters['clearance_radius_m']},
            layer=INTENT, strength=PROXY, failure_kind='intent_failure',
            reason='narrow_side_not_narrow_enough'))
        records.extend(self.standing_requirements(sim, sides, distances))
        return records

    def standing_requirements(self, sim, sides, distances):
        """Optional coarse standing query; the build protocol never treats it as navigation."""
        if not self.parameters.get('standing_query', False): return []
        collection = self.session.collection
        results = []
        for (key, vector, point), distance in zip(sides, distances):
            base = [float(point[0]), float(point[1]), float(np.arctan2(-vector[1], -vector[0]))]
            results.append({'side': key, 'role': self.roles().get(key), 'base': base,
                            'distance_m': distance,
                            'proxy': standing_proxy(sim, sim.robot.config, collection, base)})
        return [make_requirement(
            'frame_bound_side_standing_proxy', all(r['proxy'].get('available') for r in results),
            {'sides': results, 'method': 'geometry_only_standing_proxy',
             'warning': 'construction-time geometry proxy; it is not navigation evidence'},
            layer=INTENT, strength=PROXY, required=False)]

    def generate(self, sim, frames):
        config = self.config
        if config.target not in config.editable or 'move' not in config.allowed_operations: return []
        target = sim.data.xpos[sim.model.body(config.target).id]
        sides = self.sides(sim, frames)
        gates = self.preflight(sim)
        # Also require the two side rules here: moving the target is only meaningful while the
        # furniture-local distance/clearance differences are actually the goal.
        bounds = config.parameters
        near, far = sides[0], sides[1]

        def expected(pose, xy, axis):
            point = np.asarray(pose[:3])
            distances = [float(np.linalg.norm(p[:2] - point[:2])) for _, _, p in sides]
            clearances = [self.clearance(sim, p) for _, _, p in sides]
            violations = int(distances[1] - distances[0] < bounds['min_distance_difference_m'])
            violations += int(clearances[0] - clearances[1] < bounds['min_clearance_difference_m'])
            return {'violations': violations,
                    'distances_m': distances, 'clearances_m': clearances,
                    'side_labels': [k for k, _, _ in sides], 'side_roles': self.roles(),
                    'side_points_world': [p.tolist() for _, _, p in sides],
                    'frame_id': self.parameters['frame_body'],
                    'convention': 'side distances/clearances are computed in the furniture local frame',
                    'clearance_method': CLEARANCE_METHOD}

        return support_move_candidates(sim, self.session, expected=expected,
                                       yaw_candidates=[0.], region_id=self.restore_region.region_id)

    def pending_hypotheses(self):
        return ['narrow_side_passage_unsupported_by_construction',
                'far_side_manipulation_difficulty', 'solvable_side_success']
