"""有界障碍物增强：保留构建负结果，不执行机器人任务。"""
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..config import RobotConfig, read_config
from ..scenes.loading.asset_library import ThorLibrary
from ..scenes.augmentation.sampling import environment_pool, asset_geometry, surface_points
from ..scenes.tasks.eligibility import pick_type
from ..scenes.augmentation.regions import station_regions, support_regions, approach_points
from ..io import read_json, write_json, append_row, create_run
from ..planning.astar import task_support_obstacles
from ..runtime.construction.session import ObstacleEditSession
from ..runtime.construction.preparation import SettleConfig, settle_scene
from ..runtime.simulation import Simulation
from ..scenes.loading.source import SceneSource
from ..runtime.execution_config import load_execution_config
from ..scenes.stations.sampling import park_robot, sample_stations
from ..scenes.tasks.discovery import discover_tasks, robot_footprint
from ..scenes.augmentation.builder import build_variant


def augment_obstacles(config, output_dir, *, run_id, geometry_config=None, robot_config=None,
                      dataset_root=None):
    geometry_config = geometry_config or load_execution_config(config.baseline_config)
    root = Path(dataset_root) if dataset_root else create_run(Path(output_dir) / 'scene_datasets' / run_id,
        {'augmentation': asdict(config), 'geometry': asdict(geometry_config), 'seed': config.seed})
    source = SceneSource.procthor(config.dataset_dir, config.house)
    # 快照自带机器人配置；原始场景才使用显式配置或默认机器人。
    if config.input_snapshot:
        robot = RobotConfig(**read_config(Path(config.input_snapshot) / 'config.json')['robot'])
        baseline = Simulation.from_snapshot(config.input_snapshot, robot, target=config.target_instance)
    else:
        robot = robot_config or RobotConfig(
            str(Path(config.asset_library).parent.parent / 'robots/rby1m/rby1_v1.2_site_control.xml'))
        baseline = Simulation.from_source(source, robot, target=config.target_instance)
    with closing(baseline):
        tasks, _ = discover_tasks(baseline, read_json(source.metadata_path), geometry_config)
        task = next(t for t in tasks if t['instance_id'] == config.target_instance and t['operation'] == 'pick')
        task = {**task, 'support_obstacles': task_support_obstacles(baseline, task)}
        park_robot(baseline, task['anchor_world'], geometry_config)
        settle_config = SettleConfig(**config.settle)
        settle_scene(baseline, settle_config)
        candidates = sample_stations(baseline, task, geometry_config, static_only=True)
        policy = config.case_policy
        if policy and not pick_type(task):
            raise ValueError("case construction requires an eligible pick category")
        supports = support_regions(baseline, task)
        valid_count = sum(s['geometry'] == 'valid' for s in candidates)
        if not supports or valid_count < (policy.min_prefilter_points if policy else 1):
            append_row(root / 'scenes.jsonl', {'scene_id': run_id, 'status': 'rejected', 'reason': 'invalid_baseline'})
            return root
        regions = station_regions(baseline, candidates, task, geometry_config,
            robot_footprint(baseline)['radius_m'], config.corridor_band_m)
        if policy and policy.case == 'Case1':
            approach_points(baseline, candidates, task, regions)
        pool = environment_pool(baseline, task, config)
        requests = [{'categories': [category], 'size_constraints_m': {}} for category in pool['ground'] + pool['surface']]
        assets = ThorLibrary(config.asset_library).retrieve(requests, root / 'scene-files' / (run_id + '-assets'),
            limit=config.assets_per_category,
            seed=config.seed, registered_only=bool(policy and policy.registered_assets_only))
        geometry = asset_geometry(assets)
        surfaces = surface_points(supports, policy.surface_spacing_m if policy else geometry_config.spacing_m)
        if policy and policy.case == 'Case1':
            surfaces = [point for point in surfaces
                if np.linalg.norm(np.asarray(point['xy']) - task['anchor_world'][:2]) <= policy.near_target_m]
        accepted = 0
        with ObstacleEditSession(baseline, assets, settle_config) as session:
            for index, seed in enumerate(np.random.SeedSequence(config.seed).spawn(config.variant_count)):
                scene_id = f'{run_id}-v{index:03d}'
                relative = Path('scene-files') / scene_id
                try:
                    result = build_variant(session, baseline, task, config, geometry_config, pool, geometry, regions,
                        surfaces, candidates, root / relative, scene_id, int(seed.generate_state(1)[0]))
                except TimeoutError as exc:
                    result = {'scene_id': scene_id, 'status': 'rejected', 'reason': str(exc)}
                finally:
                    session.reject()  # 每个变体结束即释放候选仿真，超时也不例外。
                if result['status'] == 'accepted':
                    result['scene'] = str(relative / 'scene')
                    accepted += 1
                append_row(root / 'scenes.jsonl', result)
        if not dataset_root:
            write_json(root / 'summary.json', {'status': 'completed', 'accepted': accepted})
        return root
