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
from ...runtime.execution_config import load_execution_config
from ..augmentation.regions import support_regions
from ..geometry import body_points, body_pose
from ..loading.asset_library import AssetSelector
from ..loading.assets import RuntimeGeometryCache
from ..mode_config import load_mode_config
from ..stations.sampling import disk_samples, park_robot
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
        ctx.settings = load_mode_config(recipe.mode_config)
        ctx.execution = load_execution_config(recipe.execution_config)
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
            ctx.proxy_initializations = 0
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
