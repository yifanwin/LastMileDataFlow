"""有界障碍物增强：保留构建负结果，不执行机器人任务。"""
import logging
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import numpy as np

from ..config import RobotConfig, read_config
from ..scenes.loading.asset_library import ThorLibrary
from ..scenes.augmentation.sampling import environment_pool, asset_geometry, surface_points
from ..scenes.tasks.eligibility import pick_type
from ..scenes.augmentation.regions import station_regions, support_regions, approach_points
from ..utils.logging_config import Progress, trial_logging
from ..utils.io import read_json, write_json, append_row, create_run
from ..planning.astar import task_support_obstacles
from ..runtime.construction.session import ObstacleEditSession
from ..runtime.construction.preparation import SettleConfig, settle_scene
from ..runtime.simulation import Simulation
from ..scenes.loading.source import SceneSource
from ..runtime.execution_config import load_execution_config
from ..scenes.stations.sampling import park_robot, sample_stations
from ..scenes.tasks.discovery import discover_tasks, robot_footprint
from ..scenes.augmentation.builder import build_variant

logger = logging.getLogger(__name__)


def augment_obstacles(config, output_dir, *, run_id, geometry_config=None, robot_config=None,
                      dataset_root=None, task=None, emit=None, variant_index=None):
    # 1. 确定输出位置和采样参数。
    # config 管理障碍物种类、数量和构建预算；geometry_config 管理机器人站位采样。
    geometry_config = geometry_config or load_execution_config(config.baseline_config)
    root = Path(dataset_root) if dataset_root else create_run(Path(output_dir) / 'scene_datasets' / run_id,
        {'augmentation': asdict(config), 'geometry': asdict(geometry_config), 'seed': config.seed})
    if emit is None:
        emit = lambda result: append_row(root / 'scenes.jsonl', result)
    unit_id = f'{run_id}-v{variant_index:03d}' if variant_index is not None else run_id
    source = SceneSource.procthor(config.dataset_dir, config.house)
    progress = Progress(logger)
    logger.debug('%s: 加载增强基线', run_id)

    # 2. 加载未添加障碍物的基线场景，后续所有变体都以它为起点。
    if config.input_snapshot:
        from ..config import robot_config as parse_robot_config
        robot = parse_robot_config(read_config(Path(config.input_snapshot) / 'config.json')['robot'])
        baseline = Simulation.from_snapshot(config.input_snapshot, robot, target=config.target_instance)
    else:
        robot = robot_config or RobotConfig(
            str(Path(config.asset_library).parent.parent / 'robots/rby1m/rby1_v1.2_site_control.xml'))
        baseline = Simulation.from_source(source, robot, target=config.target_instance)
    # 正常返回或中途报错，都关闭基线仿真。
    with closing(baseline):
        # 3. 找到指定的抓取目标，准备基线并做快速几何筛选。 support_obstacles 描述目标所在的支撑物，供导航地图和支撑面提取使用。
        if task is None:
            tasks, _ = discover_tasks(baseline, read_json(source.metadata_path), geometry_config)
            task = next(t for t in tasks if t['instance_id'] == config.target_instance and t['operation'] == 'pick')
            task = {**task, 'support_obstacles': task_support_obstacles(baseline, task)}
        # 先把机器人停到目标附近，再让场景物体静置，避免从不稳定状态开始增强。
        park_robot(baseline, task['anchor_world'], geometry_config)
        settle_config = SettleConfig(**config.settle)
        settle_scene(baseline, settle_config)
        # candidates 是机器人候选站位；这里只检查静态几何，不代表真实任务成功。
        candidates = sample_stations(baseline, task, geometry_config, static_only=True)
        policy = config.case_policy
        if policy and not pick_type(task):
            raise ValueError("case construction requires an eligible pick category")
        # supports 是目标下方可用的支撑面；缺支撑面或有效站位不足就记录拒绝。
        supports = support_regions(baseline, task)
        valid_count = sum(s['geometry'] == 'valid' for s in candidates)
        if not supports or valid_count < (policy.min_prefilter_points if policy else 1):
            emit({'scene_id': unit_id, 'status': 'rejected', 'reason': 'invalid_baseline'})
            logger.warning('%s: rejected invalid_baseline（支撑面=%s, 静态有效站位=%s）', run_id, len(supports), valid_count)
            return root

        # 4. 准备地面放置点，并按场景允许的类别检索障碍物资产。
        # regions 包含地面候选点、站位间路径和网格；Case1 另补站位到目标之间的接近点。
        regions = station_regions(baseline, candidates, task, geometry_config,
            robot_footprint(baseline)['radius_m'], config.corridor_band_m)
        if policy and policy.case == 'Case1':
            approach_points(baseline, candidates, task, regions)
        # pool 区分地面/台面可用类别；assets 是实际资产，geometry 是它们的几何信息。
        pool = environment_pool(baseline, task, config)
        logger.debug('%s: 检索障碍物资产 ground=%s surface=%s', run_id, pool['ground'], pool['surface'])
        requests = [{'categories': [category], 'size_constraints_m': {}} for category in pool['ground'] + pool['surface']]
        assets = ThorLibrary(config.asset_library).retrieve(requests, root / 'scene-files' / (unit_id + '-assets'),
            limit=config.assets_per_category,
            seed=config.seed, registered_only=bool(policy and policy.registered_assets_only))
        geometry = asset_geometry(assets)

        # 5. 在支撑面上生成台面放置点，与前面的地面点 regions 分开使用。
        # Case1 只保留靠近目标的点：比较世界坐标 XY 平面距离，单位为米。
        surfaces = surface_points(supports, policy.surface_spacing_m if policy else geometry_config.spacing_m)
        if policy and policy.case == 'Case1':
            surfaces = [point for point in surfaces
                if np.linalg.norm(np.asarray(point['xy']) - task['anchor_world'][:2]) <= policy.near_target_m]
        logger.debug('%s: 放置候选准备完成 地面点=%s 台面点=%s', run_id, len(regions['points']), len(surfaces))

        # 6. 逐个构建场景变体；同一总种子派生各变体的子种子，使随机过程可复现。
        # session 管理临时编辑场景；每个变体独立尝试，不在上一个变体上累加障碍物。
        accepted = 0
        with ObstacleEditSession(baseline, assets, settle_config) as session:
            for index, seed in enumerate(np.random.SeedSequence(config.seed).spawn(config.variant_count)):
                if variant_index is not None and index != variant_index:
                    continue
                scene_id = f'{run_id}-v{index:03d}'
                relative = Path('scene-files') / scene_id
                progress.update('%s: 变体 %s/%s 放置与物理检查', run_id, index + 1, config.variant_count)
                try:
                    # 底层负责放置、碰撞/静置检查、最终站位筛选和合格场景保存。
                    with trial_logging(f'variant={scene_id}', progress):
                        result = build_variant(session, baseline, task, config, geometry_config, pool, geometry, regions,
                            surfaces, candidates, root / relative, scene_id, int(seed.generate_state(1)[0]))
                except TimeoutError as exc:
                    # 超时是本变体的拒绝结果，继续下一个；其他异常仍向外抛出。
                    result = {'scene_id': scene_id, 'status': 'rejected', 'reason': str(exc)}
                finally:
                    # reject 只释放临时候选；已通过检查并写入磁盘的场景不受影响。
                    session.reject()  # 每个变体结束即释放候选仿真，超时也不例外。
                if result['status'] == 'accepted':
                    result['scene'] = str(relative / 'scene')
                    result['before_scene'] = str(relative / 'before-scene')
                    accepted += 1
                # 接受和拒绝都写入同一主记录；接受项额外引用保存的场景相对路径。
                emit(result)
                if result['status'] == 'accepted':
                    logger.debug('%s: accepted', scene_id)
                else:
                    logger.warning('%s: %s %s', scene_id, result['status'], result.get('reason', ''))
                progress.update('%s 已处理变体=%s/%s 接受=%s 拒绝=%s', run_id, index + 1,
                                config.variant_count, accepted, index + 1 - accepted)

        progress.update('%s 增强收尾 已处理变体=%s/%s 接受=%s 拒绝=%s', run_id, config.variant_count,
                        config.variant_count, accepted, config.variant_count - accepted, force=True)

        # 7. 独立运行才写本地汇总；写入父场景库时由上层统一汇总，避免重复记录。
        if not dataset_root:
            write_json(root / 'summary.json', {'status': 'completed', 'accepted': accepted})
            from ..data.visualization.scene_preview import export_scene_previews
            export_scene_previews(root)
        return root
