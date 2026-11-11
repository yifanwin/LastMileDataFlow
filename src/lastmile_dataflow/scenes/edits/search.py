"""One owned baseline and shared geometric preparation per discovered task."""
import copy
from dataclasses import asdict, dataclass, field
import time

import numpy as np

from ...data.rendering.head_views import robocasa_camera_calibration
from ...planning.navigation.astar import scene_occupancy, task_support_obstacles
from ...runtime.construction.preparation import SettleConfig, preparation_guard, settle_scene
from ...runtime.construction.session import ObstacleEditSession, clone_preparation
from ...runtime.simulation import InitializationError
from .placement import support_regions
from ..geometry import body_points, body_pose
from ..loading.assets import AssetSelector
from ..loading.assets import RuntimeGeometryCache
from ..stations.sampling import disk_samples, park_robot, select_task_stations
from pathlib import Path
from .placement import BuildProposal, propose_obstacles, resolve_assets
from .checks import proposal_scene
from ..tasks.discovery import grasp_candidates, robot_footprint


@dataclass
class TaskGeometry:
    task: dict
    target_points: np.ndarray
    supports: list
    lattice: list
    occupancy: object
    navigation: object
    clearance_base: np.ndarray
    chassis: dict
    navigation_footprint: dict
    pose_checks: dict = field(default_factory=dict)
    proxy_obstacles: object = None


def task_geometry(sim, source_task, config):
    task = copy.deepcopy(source_task)
    target = sim.model.body(task['target_body']).id
    points = body_points(sim, target)
    if task['operation'] == 'pick':
        task['anchor_world'] = ((points.min(axis=0) + points.max(axis=0)) / 2).tolist()
    else:
        handle = sim.model.body(task['handle_body']).id
        pose = body_pose(sim, handle)
        center = np.asarray(task['handle_local_bounds']).mean(axis=0)
        task['anchor_world'] = (pose[:3, :3] @ center + pose[:3, 3]).tolist()
        joint = sim.model.joint(task['joint_name']).id
        current = float(sim.data.qpos[sim.model.jnt_qposadr[joint]])
        direction = np.sign(task['joint_goal'] - task['joint_initial'])
        if direction * (task['joint_goal'] - current) <= 1e-4:
            raise ValueError('articulation_already_open_after_preparation')
        task['joint_initial'] = current
        task['joint_axis_world'] = sim.data.xaxis[joint].tolist()
        task['joint_anchor_world'] = sim.data.xanchor[joint].tolist()
    task['support_obstacles'] = task_support_obstacles(sim, task)
    surfaces = support_regions(sim, task) if task['operation'] == 'pick' else []
    if task['operation'] == 'pick' and not surfaces:
        raise ValueError('target_has_no_valid_support_surface')
    chassis = robot_footprint(sim)
    footprint = robot_footprint(sim, whole_body=config.navigation_whole_body)
    occupancy = scene_occupancy(sim, task['anchor_world'], None, config.map_resolution_m,
        support_obstacles=task['support_obstacles'],
        robot_height=footprint['max'][2] if config.navigation_whole_body else .4)
    return TaskGeometry(task, points, surfaces,
        disk_samples(task['anchor_world'], config.radius_m, config.spacing_m), occupancy,
        occupancy.navigation(footprint['radius_m'], config.navigation_margin_m),
        occupancy.clearance_base(chassis['radius_m']), chassis, footprint)


class BuildContext:
    """Baseline is read-only after prepare; edited candidates derive new geometry."""
    @classmethod
    def prepare(cls, source_sim, source_task, recipe):
        preparation_guard(source_sim)
        ctx = cls()
        ctx.recipe, ctx.station_config = recipe, recipe.geometry
        ctx.settings = recipe.edits
        ctx.baseline = clone_preparation(source_sim)
        ctx.assets = ctx.session = None
        ctx.timings = {}
        try:
            ctx.baseline.target_id = ctx.baseline.model.body(source_task['target_body']).id
            task = copy.deepcopy(source_task)
            grasps = grasp_candidates(task, recipe.assets_dir, allow_fallback=False)
            if not grasps:
                raise ValueError('task_has_no_valid_data_grasps' if task['operation'] == 'pick' else 'task_has_no_contact_definition')
            if task['operation'] == 'pick':
                task['require_data_grasps'] = True
            ctx.grasp_evidence = {'count': len(grasps), 'sources': sorted({row['source'] for row in grasps})}
            ctx.grasps = grasps
            start = time.monotonic()
            ctx.parking = park_robot(ctx.baseline, task['anchor_world'], ctx.station_config)
            ctx.settle_config = SettleConfig(**recipe.settle)
            ctx.settling = settle_scene(ctx.baseline, ctx.settle_config,
                                       require_stability=ctx.settle_config.require_source_stability)
            if not ctx.settling['valid']:
                raise InitializationError('task_baseline_settling_failed')
            ctx.timings['parking_and_settle_s'] = time.monotonic() - start
            start = time.monotonic()
            ctx.geometry = task_geometry(ctx.baseline, task, ctx.station_config)
            ctx.timings['geometry_s'] = time.monotonic() - start
            ctx.profile = asdict(ctx.baseline.robot.config)
            ctx.camera_calibration = None
            if ctx.baseline.robot.config.control_profile == 'robocasa-v1':
                ctx.camera_calibration = robocasa_camera_calibration(ctx.baseline)
                if not ctx.camera_calibration['valid']:
                    raise ValueError('compiled_robocasa_camera_profile_mismatch')
            ctx.selector = AssetSelector(recipe.asset_index)
            ctx.assets = RuntimeGeometryCache([p.geometry_entry() for p in ctx.selector.profiles if p.loadable])
            ctx.session = ObstacleEditSession(ctx.baseline, ctx.assets, ctx.settle_config)
            ctx.station_checks = {}
            ctx.baseline_snapshot = None
            return ctx
        except BaseException:
            ctx.close()
            raise

    @property
    def task(self):
        return self.geometry.task

    def derive(self, sim):
        return task_geometry(sim, self.task, self.station_config)

    def close(self):
        if self.session is not None:
            self.session.close()
        if self.assets is not None:
            self.assets.close()
        self.baseline.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _freeze_natural_baseline(ctx, root, prefix, baseline_cache):
    """Reuse one frozen scene per house when only the robot parking differs."""
    if ctx.baseline_snapshot is not None:
        return True
    cached = baseline_cache.get('baseline') if baseline_cache is not None else None
    mask = np.ones(ctx.baseline.model.nq, bool)
    mask[list(ctx.baseline.robot.addresses.values())] = False
    qpos = ctx.baseline.data.qpos[mask].copy()
    if cached is not None and np.allclose(cached['qpos'], qpos, rtol=0, atol=5e-3):
        ctx.baseline_snapshot = cached['path']
        return True
    ctx.baseline_snapshot = Path('scene-files') / prefix / 'baseline'
    ctx.baseline.freeze(Path(root) / ctx.baseline_snapshot)
    if baseline_cache is not None and cached is None:
        baseline_cache['baseline'] = {'path': ctx.baseline_snapshot, 'qpos': qpos}
    return False


def build_natural_task(ctx, root, *, house, seed, baseline_cache=None):
    """Freeze the unedited task baseline and its valid stations; no difficulty construction."""
    prefix = f'{ctx.baseline.source.split}{house}-{ctx.task["task_id"]}'
    scene_id = f'{prefix}-no_edit-v000'
    proposal = BuildProposal('no_edit-000', 'no_edit')
    stations, sampling = select_task_stations(ctx, ctx.baseline, ctx.geometry, proposal,
                                              measure_visibility=False)
    metrics = {'proposals': 1, 'cheap_rejected': 0, 'physical_candidates': 0,
               'physical_trials': 0, 'repairs': 0, 'asset_compiles': 0,
               'station_initializations': sampling['new_initializations'],
               'proxy_initializations': 0,
               'final_station_initializations': sampling['new_initializations']}
    record = {'scene_id': scene_id, 'proposal_intent': 'no_edit', 'build_metrics': metrics,
              'house': house, 'task_id': ctx.task['task_id'], 'seed': seed,
              'source_split': ctx.baseline.source.split,
              'dataset_split': ctx.recipe.house_splits[str(house)]}
    if not stations:
        return {**record, 'status': 'rejected', 'reason': 'no_valid_initial_stations',
                'last_checks': {'station_evidence': sampling['rejected_stations']}}
    _freeze_natural_baseline(ctx, root, prefix, baseline_cache)
    reused = baseline_cache is not None and baseline_cache.get('baseline', {}).get('path') == ctx.baseline_snapshot
    for station in stations:
        cell = ctx.geometry.navigation.cell(station['xy'])
        station['clearance_base_m'] = float(ctx.geometry.clearance_base[cell]) if cell is not None else None
    return {**record, 'status': 'accepted', 'scene': str(ctx.baseline_snapshot),
            'task': ctx.geometry.task, 'stations': stations, 'station_hints': [],
            'edits': proposal.runtime_changes(),
            'proposal_metadata': {'construction': 'no_edit', 'baseline_reused': reused},
            'construction_checks': {'valid': True, 'path': 'no_edit'},
            'station_sampling': sampling, 'task_completion': 'unknown'}




def _freeze_candidate(ctx, sim, geometry, proposal, stations, sampling, checks, root, scene_id):
    location = Path('scene-files') / scene_id
    sim.freeze(Path(root) / location)
    for station in stations:
        cell = geometry.navigation.cell(station['xy'])
        station['clearance_base_m'] = float(geometry.clearance_base[cell]) if cell is not None else None
    return {'scene': str(location), 'task': geometry.task, 'stations': stations,
            'station_hints': [], 'edits': proposal.runtime_changes(),
            'proposal_metadata': {}, 'construction_checks': checks,
            'station_sampling': sampling, 'task_completion': 'unknown'}


def search_edit(ctx, root, scene_id, rng):
    deadline = time.monotonic() + ctx.settings.variant_timeout_s
    rejected = {}
    metrics = {'proposals': 0, 'physical_trials': 0, 'station_initializations': 0}
    result = {'scene_id': scene_id, 'proposal_intent': 'edit', 'status': 'rejected',
              'build_metrics': metrics, 'rejection_counts': rejected}
    for attempt in range(ctx.settings.max_proposals):
        if time.monotonic() >= deadline:
            return {**result, 'reason': 'variant_timeout'}
        proposal = propose_obstacles(ctx, rng, attempt)
        metrics['proposals'] += 1
        reason = 'no_candidate_placement'
        if proposal is not None:
            try:
                proposal = resolve_assets(ctx, proposal)
                metrics['physical_trials'] += 1
                with proposal_scene(ctx, proposal, deadline=deadline) as (sim, geometry, checks):
                    if checks['valid']:
                        stations, sampling = select_task_stations(ctx, sim, geometry, proposal)
                        metrics['station_initializations'] += sampling['new_initializations']
                        if stations:
                            return {**result, **_freeze_candidate(ctx, sim, geometry, proposal, stations,
                                    sampling, checks, root, scene_id), 'status': 'accepted'}
                        reason = 'no_valid_initial_stations'
                    else:
                        reason = checks.get('reason', 'physical_validation_failed')
            except TimeoutError:
                return {**result, 'reason': 'variant_timeout'}
            except ValueError as exc:
                reason = str(exc)
        rejected[reason] = rejected.get(reason, 0) + 1
    return {**result, 'reason': 'proposal_budget_exhausted'}


def build_task(ctx, root, *, house, seed, baseline_cache=None):
    yield build_natural_task(ctx, root, house=house, seed=seed, baseline_cache=baseline_cache)
    if not ctx.settings.enabled:
        return
    prefix = f'{ctx.baseline.source.split}{house}-{ctx.task["task_id"]}'
    result = search_edit(ctx, root, prefix + '-edit-v000', np.random.default_rng(seed))
    yield {**result, 'house': house, 'task_id': ctx.task['task_id'], 'seed': seed,
           'source_split': ctx.baseline.source.split, 'dataset_split': ctx.recipe.house_splits[str(house)]}
