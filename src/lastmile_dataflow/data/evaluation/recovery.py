"""Recovery regions and diagnostics derived from measured starting stations."""
from pathlib import Path
import math

import numpy as np

from ...planning.astar import Grid, distance_field, segment_free, search_region


def yaw_gap(a, b):
    return (np.asarray(a) - b + math.pi) % (2 * math.pi) - math.pi


class RecoveryRegion:
    def __init__(self, grid, measured, patches, radius_m, yaw_tolerance_rad):
        self.grid = grid
        self.measured = measured
        self.patches = patches
        self.radius_m = radius_m
        self.yaw_tolerance_rad = yaw_tolerance_rad
        self.distances = [{(y, x): d for y, x, d in patch['cells']} for patch in patches]

    @classmethod
    def build(cls, grid, measured, config, navigation=None):
        patches = []
        for station in measured:
            if not station['high']:
                continue
            if navigation is not None:
                patch = navigation.recovery_patch(station, config.region_radius_m, config.region_yaw_tolerance_rad)
                if patch is not None:
                    patches.append(patch)
                continue
            xy = station['xy']
            cell = grid.cell(xy)
            if cell is None or not segment_free(grid, xy, grid.xy(cell)):
                continue
            distances = distance_field(grid, xy, config.region_radius_m)
            distances += np.linalg.norm(np.asarray(xy) - grid.xy(cell))
            cells = [[int(y), int(x), float(distances[y, x])]
                     for y, x in np.argwhere(distances <= config.region_radius_m + 1e-9)]
            patches.append({'anchor': station, 'cells': cells})
        return cls(grid, measured, patches, config.region_radius_m, config.region_yaw_tolerance_rad)

    def contains(self, xy, yaw):
        # Nearest measured non-high stations clip expanded patches. The exact
        # measured position is used, so a low start is never an initial recovery.
        neighbors = [(float(np.linalg.norm(np.asarray(xy) - s['xy'])), s['high'])
                     for s in self.measured if abs(yaw_gap(yaw, s['yaw'])) <= self.yaw_tolerance_rad + 1e-9]
        if not neighbors or not min(neighbors, key=lambda item: (round(item[0], 9), item[1]))[1]:
            return False
        for patch, distances in zip(self.patches, self.distances):
            anchor = patch['anchor']
            if abs(yaw_gap(yaw, anchor['yaw'])) > self.yaw_tolerance_rad + 1e-9:
                continue
            if 'boxes' in patch:
                query = np.r_[xy, yaw_gap(yaw, anchor['yaw'])]
                if (np.linalg.norm(np.asarray(xy) - anchor['xy']) <= 1e-9
                        and abs(query[2]) <= 1e-9):
                    return True
                if any(np.all(query >= np.asarray(box[:3]) - 1e-9)
                       and np.all(query <= np.asarray(box[3:]) + 1e-9) for box in patch['boxes']):
                    return True
                continue
            cell = self.grid.cell(xy)
            if cell is None or not self.grid.free[cell]:
                continue
            anchor_cell = self.grid.cell(anchor['xy'])
            if cell == anchor_cell and segment_free(self.grid, anchor['xy'], xy):
                local_length = float(np.linalg.norm(np.asarray(xy) - anchor['xy']))
            else:
                local_length = distances.get(cell, float('inf')) + float(np.linalg.norm(np.asarray(xy) - self.grid.xy(cell)))
            if local_length <= self.radius_m + 1e-9 and segment_free(self.grid, self.grid.xy(cell), xy):
                return True
        return False

    def goals(self):
        goals = []
        seen = set()
        for patch in self.patches:
            anchor = patch['anchor']
            if 'boxes' in patch:
                poses = [[*anchor['xy'], anchor['yaw']], *[
                    [*(np.mean([box[:3], box[3:]], axis=0)[:2]),
                     anchor['yaw'] + (box[2] + box[5]) / 2] for box in patch['boxes']]]
                for x, y, yaw in poses:
                    key = (x, y, yaw)
                    if key not in seen and self.contains([x, y], yaw):
                        goals.append({'xy': [x, y], 'yaw': yaw, 'anchor_station_id': anchor['station_id']})
                        seen.add(key)
                continue
            points = [anchor['xy'], *(self.grid.xy((y, x)).tolist() for y, x, _ in patch['cells'])]
            for xy in points:
                key = (*xy, anchor['yaw'])
                if key not in seen and self.contains(xy, anchor['yaw']):
                    goals.append({'xy': xy, 'yaw': anchor['yaw'], 'anchor_station_id': anchor['station_id']})
                    seen.add(key)
        return goals

    def to_dict(self):
        return {'method': ('geometry_sampled_pose_boxes_nearest_measurement_clip'
                           if any('boxes' in p for p in self.patches) else 'geodesic_patches_nearest_measurement_clip'),
                'origin': self.grid.origin.tolist(), 'resolution_m': self.grid.resolution,
                'free': self.grid.free.tolist(), 'measured': self.measured, 'patches': self.patches,
                'radius_m': self.radius_m, 'yaw_tolerance_rad': self.yaw_tolerance_rad}

    @classmethod
    def from_dict(cls, value):
        grid = Grid(np.asarray(value['origin']), value['resolution_m'], np.asarray(value['free'], dtype=bool))
        return cls(grid, value['measured'], value['patches'], value['radius_m'], value['yaw_tolerance_rad'])


def difficulty(region, start, candidates, local_radius_m=None, *, reference_path=None):
    path = dict(reference_path) if reference_path is not None else search_region(region.grid, start['xy'], region.goals())
    if path is None:
        return None
    reachable = path.pop('reachable_goals')
    euclidean = min(float(np.linalg.norm(np.asarray(start['xy']) - goal['xy'])) for goal in reachable)
    local = [s for s in candidates if local_radius_m is None or
             np.linalg.norm(np.asarray(start['xy']) - s['xy']) <= local_radius_m + 1e-9]
    recovered = sum(region.contains(s['xy'], s['base'][2]) for s in local)
    return {'path_length_m': path['length_m'], 'euclidean_distance_m': euclidean,
            'path_metric': path.get('method', 'grid_geodesic'),
            'euclidean_distance_scope': path.get('goal_reachability_scope', 'reachable_goals'),
            'detour_factor': path['length_m'] / euclidean if euclidean > 1e-9 else None,
            'detour_status': 'defined' if euclidean > 1e-9 else 'zero_euclidean_distance',
            'recovery_ratio': recovered / len(local) if local else None,
            'recovery_candidates': recovered, 'local_candidates': len(local),
            'oracle_path': path}


def motion_diagnostics(trajectory, region, config, *, recording_complete):
    """Read the one actual trajectory; no resimulation or inferred motion."""
    unknown = {'recovery_success': None, 'recovery_first_time_s': None,
               'base_path_length_m': None, 'final_displacement_m': None,
               'max_displacement_m': None, 'base_yaw_travel_rad': None,
               'final_yaw_delta_rad': None, 'max_yaw_delta_rad': None,
               'effective_adjustment': None, 'diagnostic_status': 'trajectory_unavailable'}
    if not Path(trajectory).exists():
        return unknown
    with np.load(trajectory, allow_pickle=False) as trace:
        if 'base_xyyaw' not in trace:
            return {**unknown, 'diagnostic_status': 'base_pose_unavailable'}
        base, time = trace['base_xyyaw'], trace['time_s']
    if len(base) < 2 or base.shape != (len(time), 3) or not np.isfinite(base).all() or not np.isfinite(time).all():
        return {**unknown, 'diagnostic_status': 'invalid_or_empty_base_trace'}
    displacement = np.linalg.norm(base[:, :2] - base[0, :2], axis=1)
    yaw = yaw_gap(base[:, 2], base[0, 2])
    adjusted = np.maximum.accumulate((displacement >= config.effective_translation_m) |
                                    (np.abs(yaw) >= config.effective_yaw_rad))
    entered = next((i for i, pose in enumerate(base) if adjusted[i] and region.contains(pose[:2], pose[2])), None)
    return {'recovery_success': True if entered is not None else False if recording_complete else None,
            'recovery_first_time_s': float(time[entered] - time[0]) if entered is not None else None,
            'base_path_length_m': float(np.linalg.norm(np.diff(base[:, :2], axis=0), axis=1).sum()),
            'final_displacement_m': float(displacement[-1]), 'max_displacement_m': float(displacement.max()),
            'base_yaw_travel_rad': float(np.abs(yaw_gap(base[1:, 2], base[:-1, 2])).sum()),
            'final_yaw_delta_rad': float(yaw[-1]), 'max_yaw_delta_rad': float(np.abs(yaw).max()),
            'effective_adjustment': bool(adjusted[-1]),
            'diagnostic_status': 'complete' if recording_complete else 'partial_trace'}
