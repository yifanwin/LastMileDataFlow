"""Read existing trial evidence and measure independent, unstarted planning views."""
from contextlib import closing
from pathlib import Path

import numpy as np

from ...data.rendering.head_views import TaskVisibility
from ...planning.astar import scene_occupancy, task_support_obstacles, search, segment_free
from ...runtime.execution.context import clone_sim
from ...runtime.execution.protocol import SimulationState
from ...scenes.dataset_config import SceneStationConfig
from ...scenes.geometry import body_pose
from ...scenes.stations.sampling import robot_contacts
from ...scenes.stations.initialization import floor_support
from ...scenes.tasks.discovery import robot_footprint
from ...utils.io import read_json


def planning_evidence(root, trials):
    """Only explicitly bound phases are consumed, never inferred from old trace order."""
    evidence = {}
    for trial in trials:
        reference = trial.get('planning_ref')
        path = Path(root) / reference if reference else None
        attempts, seen = [], set()
        if path is not None and path.is_file():
            for row in read_json(path):
                diagnostic = {**row, **row.get('diagnostics', {})}
                if not diagnostic.get('phase_evidence'):
                    continue
                identity = diagnostic.get('identity')
                if identity and (identity['attempt_id'] != trial['rollout_id'] or identity['seed'] != trial['seed']
                                 or identity['scene_id'] != trial['scene_id'] or identity['trial'] != trial['trial']):
                    raise ValueError('planning evidence lane/trial binding mismatch')
                if identity and 'station_index' in trial and identity['station_index'] != trial['station_index']:
                    raise ValueError('planning evidence station binding mismatch')
                input_ref = diagnostic.get('grasp_inputs_ref')
                if not input_ref or not Path(input_ref).is_file():
                    continue
                inputs = read_json(input_ref)
                slot = diagnostic.get('batch_slot', 0)
                if inputs['seeds'][slot] != trial['seed']:
                    raise ValueError('planning evidence input seed mismatch')
                key = (input_ref, slot)
                if key in seen:
                    continue
                seen.add(key)
                attempts.append({**diagnostic, 'planning_ref': str(path.resolve())})
        evidence[trial['rollout_id']] = {'status': 'known' if attempts else 'unresolved', 'attempts': attempts}
    return evidence


class PreparedMeasurements:
    """One task's read-only views, reused for geometry and the optional GPU probe."""
    def __init__(self, baseline, scene, trials, planning, config, *, min_reference_pixels=24):
        self.baseline, self.scene, self.config = baseline, scene, config
        self.planning = planning
        self.min_reference_pixels = min_reference_pixels
        self.trials = {station['station_id']: [] for station in scene['stations']}
        for trial in trials:
            self.trials[trial['station_id']].append(trial)
        self.references, self.states, self.measurements, self.grids, self.paths = {}, {}, {}, {}, {}
        for station in scene['stations']:
            attempts = [attempt for trial in self.trials[station['station_id']]
                        for attempt in planning.get(trial['rollout_id'], {}).get('attempts', [])]
            reference = next((a.get('planning_state_ref') for a in attempts if a.get('planning_state_ref')), None)
            self.references[station['station_id']] = reference

    def view(self, station_id):
        reference = self.references[station_id]
        if not reference or not Path(reference).is_file():
            return None
        sim = clone_sim(self.baseline, target=self.scene['task']['target_body'],
                        handle=self.scene['task'].get('handle_body'))
        try:
            sim.restore_snapshot(reference)
        except BaseException:
            sim.close()
            raise
        return sim

    def measure(self):
        task, config = self.scene['task'], self.config
        camera_config = SceneStationConfig(width=config.width, height=config.height,
            min_reference_pixels=self.min_reference_pixels, min_target_pixels=config.min_target_pixels)
        with TaskVisibility(self.baseline, task, camera_config) as visibility:
            for station in self.scene['stations']:
                identity = station['station_id']
                sim = self.view(identity)
                if sim is None:
                    self.measurements[identity] = {'status': 'unresolved', 'reason': 'missing_planning_state',
                                                   'visibility': {'complete': False, 'value': None, 'known_max': None}}
                    continue
                with closing(sim):
                    self.states[identity] = SimulationState.capture(sim)
                    base = sim.robot.base_pose()
                    pose = body_pose(sim, sim.model.body(task['target_body']).id)
                    if task['operation'] == 'open':
                        handle = body_pose(sim, sim.model.body(task['handle_body']).id)
                        anchor = handle[:3, :3] @ np.asarray(task['handle_local_bounds']).mean(axis=0) + handle[:3, 3]
                    else:
                        from ...scenes.geometry import body_points
                        points = body_points(sim, sim.model.body(task['target_body']).id)
                        anchor = (points.min(0) + points.max(0)) / 2
                    # Exported assets may be y-up: project a stable body axis
                    # onto the floor instead of treating local y as lateral.
                    horizontal = pose[:2, :3].T
                    axis = next(axis for axis in horizontal if np.linalg.norm(axis) >= .5)
                    axis = axis / np.linalg.norm(axis)
                    lateral = np.array([-axis[1], axis[0]])
                    relative = np.array([np.dot(base[:2] - anchor[:2], axis),
                                         np.dot(base[:2] - anchor[:2], lateral)])
                    chassis = robot_footprint(sim)
                    footprint = robot_footprint(sim, whole_body=config.navigation_whole_body)
                    occupancy = scene_occupancy(sim, anchor, None, config.map_resolution_m,
                        support_obstacles=task_support_obstacles(sim, task),
                        robot_height=footprint['max'][2] if config.navigation_whole_body else .4)
                    grid = occupancy.navigation(footprint['radius_m'], config.navigation_margin_m)
                    self.grids[identity] = grid
                    cell = grid.cell(base[:2])
                    if cell is None:
                        self.measurements[identity] = {'status': 'unresolved', 'reason': 'prepared_base_outside_floor_grid'}
                        continue
                    self.measurements[identity] = {'status': 'measured', 'state_ref': self.references[identity],
                        'base': base.tolist(), 'yaw': float(base[2]), 'target_anchor_world': anchor.tolist(),
                        'target_distance_m': float(np.linalg.norm(base[:2] - anchor[:2])),
                        'base_in_target_frame': relative.tolist(),
                        'target_planar_axes_world': [axis.tolist(), lateral.tolist()],
                        'non_base_initial': {k: v for k, v in station['initial'].items() if k != sim.robot.initial_base_field},
                        'initialization_valid': bool(floor_support(sim, base[:2]) and not robot_contacts(sim)),
                        'visibility': visibility.measure(sim),
                        'clearance_base_m': float(occupancy.clearance_base(chassis['radius_m'])[cell]),
                        'clearance_definition': 'distance_to_occupied_minus_physical_chassis_enclosing_radius',
                        'chassis_radius_m': chassis['radius_m'], 'navigation_radius_m': footprint['radius_m'],
                        'navigation_margin_m': config.navigation_margin_m}
        return self.measurements

    def path(self, start, goal):
        key = (start['station_id'], goal['station_id'])
        if key not in self.paths:
            a, b = (self.measurements.get(identity, {}) for identity in key)
            if a.get('status') != 'measured' or b.get('status') != 'measured':
                result = {'status': 'unresolved', 'reason': 'missing_prepared_navigation_state'}
            else:
                grid = self.grids[key[0]]
                begin, end = a['base'][:2], b['base'][:2]
                route = search(grid, begin, end)
                result = {'status': 'measured', 'segment_free': segment_free(grid, begin, end),
                          'path_exists': route is not None, 'path_length_m': route['length_m'] if route else None}
            self.paths[key] = result
        return self.paths[key]
