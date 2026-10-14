"""场景构造、逐点采集、LastMile 数据与评测的命令入口。"""
import argparse
from dataclasses import replace
from pathlib import Path

from .config import CollectionConfig, RobotConfig, load_config
from .io import read_json

ROOT = Path(__file__).resolve().parents[2]


def parser():
    """声明全部子命令与参数。"""
    p = argparse.ArgumentParser(prog="lastmile-dataflow")
    sub = p.add_subparsers(dest="command", required=True)
    lastmile = sub.add_parser('collect-lastmile-datasets', help='scene packages -> cuRobo calibration -> continuous recovery -> audited raw-first publication')
    lastmile.add_argument('--dataset', required=True, type=Path)
    lastmile.add_argument('--assets-dir', required=True, type=Path)
    lastmile.add_argument('--config', type=Path, default=ROOT/'configs/lastmile_datasets/v0.json')
    lastmile.add_argument('--output-dir', type=Path, default=ROOT/'outputs')
    lastmile.add_argument('--run-id', required=True)
    lastmile.add_argument('--gpu-ids', nargs='+', type=int, default=list(range(8)))
    lastmile.add_argument('--workers', type=int, default=1)
    lastmile.add_argument('--samples', nargs='+', help='explicit sample IDs; all matching configuration packages are included')
    lastmile.add_argument('--resume', action='store_true')
    lastmile.add_argument('--calibration-only', action='store_true')
    lastmile.add_argument('--preflight-only', action='store_true', help='verify immutable inputs without GPU execution')
    lastmile.add_argument('--max-trials', type=int, help='diagnostic per-package attempt limit; not a formal full calibration')
    lastmile.add_argument('--no-molmobot-export', action='store_true', help='keep audited raw-first data; derive model packages separately')
    bench_build = sub.add_parser('build-lastmilebench', help='freeze a minimal MolmoBot-native benchmark from scene_datasets')
    bench_build.add_argument('--dataset', required=True, type=Path)
    bench_build.add_argument('--output', required=True, type=Path)
    bench_build.add_argument('--policy-config', type=Path, default=ROOT/'configs/policies/molmobot.json')
    bench_build.add_argument('--gpu-ids', nargs='+', type=int, default=list(range(8)))
    bench_build.add_argument('--starts-per-sample', type=int, default=1)
    bench_build.add_argument('--calibration-dir', type=Path)
    bench_build.add_argument('--allow-uncalibrated-smoke', action='store_true')
    bench_build.add_argument('--seed', type=int, default=20261012)
    bench_build.add_argument('--horizon', type=int, default=400)
    bench_build.add_argument('--wall-timeout-s', type=float, default=600.)
    bench_build.add_argument('--radius-m', type=float, default=1.5)
    bench_eval = sub.add_parser('evaluate-lastmilebench', help='real MolmoBot VLA episodes and task success rate')
    bench_eval.add_argument('--benchmark', required=True, type=Path)
    bench_eval.add_argument('--output', required=True, type=Path)
    bench_eval.add_argument('--policy-config', type=Path, default=ROOT/'configs/policies/molmobot.json')
    bench_eval.add_argument('--gpu-ids', nargs='+', type=int, default=list(range(8)))
    bench_eval.add_argument('--log-dir', type=Path, default=ROOT/'outputs/logs')
    discovery = sub.add_parser('discover-scene-datasets', help='bounded task discovery with spatial target coverage; no strategy execution')
    discovery.add_argument('--config', required=True, type=Path)
    discovery.add_argument('--output', required=True, type=Path)
    qualification = sub.add_parser('qualify-scene-datasets', help='refine spatial choices with full visible station initialization')
    qualification.add_argument('--discovery', required=True, type=Path)
    qualification.add_argument('--output', required=True, type=Path)
    scene_build = sub.add_parser('build-scene-datasets', help='construct and seal strategy-independent scene_datasets')
    scene_build.add_argument('--config', required=True, type=Path)
    scene_build.add_argument('--discovery', required=True, type=Path)
    scene_build.add_argument('--output-dir', type=Path, default=ROOT/'outputs')
    scene_build.add_argument('--dataset-id', required=True)
    scene_build.add_argument('--gpu-ids', nargs='+', type=int, required=True)
    scene_build.add_argument('--workers', type=int, default=2)
    scene_build.add_argument('--resume', action='store_true')
    scene_build.add_argument('--allow-partial-targets', action='store_true', help='retain original quota; publish only qualified samples with explicit partial status')
    scene_unit = sub.add_parser('scene-dataset-unit', help=argparse.SUPPRESS)
    scene_unit.add_argument('--dataset', required=True, type=Path)
    scene_unit.add_argument('--unit-index', required=True, type=int)
    obstacles = sub.add_parser('augment-obstacles', help='pure procedural obstacle construction and paired real RGB; no Agent calls')
    obstacles.add_argument('--config', required=True, type=Path)
    obstacles.add_argument('--output-dir', type=Path, default=ROOT/'outputs')
    obstacles.add_argument('--run-id', required=True)
    molmobot_export = sub.add_parser('export-molmobot', help='derive MolmoBot state22/images/native action without changing source')
    molmobot_export.add_argument('--attempt', required=True, type=Path)
    molmobot_export.add_argument('--output', required=True, type=Path)
    molmobot_export.add_argument('--state-images-only', action='store_true', help='no invented motor action for position grippers')
    molmobot_export.add_argument('--gripper-map', type=Path, help='explicit calibrated position-to-motor JSON mapping')
    molmobot_export.add_argument('--seed', type=int, default=0)
    molmobot_export.add_argument('--chunk-length', type=int, default=16)
    molmobot_export.add_argument('--conditioning-point', nargs=2, type=float, metavar=('X', 'Y'), help='open: normalized first raw head image point')
    raw = sub.add_parser('collect-no-edit', help='raw val scenes: station trials, Gaussian maps and continuous 1–3 successful rollouts')
    raw.add_argument('--assets-dir', required=True, type=Path)
    raw.add_argument('--dataset-dir', type=Path)
    raw.add_argument('--robot-config', type=Path, help='optional existing RBY1M configuration')
    raw.add_argument('--config', type=Path, default=ROOT/'configs/no_edit/curobo_v080.json')
    raw.add_argument('--output-dir', type=Path, default=ROOT/'outputs')
    raw.add_argument('--run-id', required=True)
    raw.add_argument('--gpu-ids', nargs='+', type=int, default=list(range(8)))
    raw.add_argument('--workers', type=int, default=2)
    raw.add_argument('--houses', nargs='+', type=int)
    raw.add_argument('--targets', nargs='+', help='explicit subset: instance IDs, asset IDs or task IDs')
    raw.add_argument('--spacing-m', type=float)
    raw.add_argument('--trials-per-station', type=int)
    raw.add_argument('--max-tasks', type=int, help='explicit per-house smoke subset, not full collection')
    raw.add_argument('--max-trials', type=int, help='explicit smoke budget, leaves unfinished tasks incomplete')
    raw.add_argument('--resume', action='store_true')
    raw.add_argument('--index-only', action='store_true')
    raw.add_argument('--no-video', action='store_true', help='retain RGB but disable MP4')
    raw.add_argument('--collection-config', type=Path,
                     help='explicit CollectionConfig JSON; without it the raw-first defaults apply')
    status = sub.add_parser('no-edit-status', help='read no-edit batch progress without starting physics')
    status.add_argument('run', type=Path)
    return p


def main(argv=None):
    """按子命令分发。注意每个分支最后都返回退出码，供 CI/脚本判断。"""
    args = parser().parse_args(argv)
    if args.command == 'build-lastmilebench':
        from .workflows.lastmilebench import build
        print(build(args.dataset,args.output,args.policy_config,gpu_ids=args.gpu_ids,
            starts_per_sample=args.starts_per_sample,calibration_dir=args.calibration_dir,
            allow_uncalibrated=args.allow_uncalibrated_smoke,seed=args.seed,horizon=args.horizon,
            wall_timeout_s=args.wall_timeout_s,radius_m=args.radius_m))
        return 0
    if args.command == 'evaluate-lastmilebench':
        from .workflows.lastmilebench import evaluate
        output = evaluate(args.benchmark,args.output,args.policy_config,gpu_ids=args.gpu_ids,log_dir=args.log_dir)
        print(read_json(output/'summary.json'))
        return 0 if read_json(output/'summary.json')['complete'] else 1
    if args.command == 'discover-scene-datasets':
        from .workflows.scene_datasets import discover_scene_dataset
        print(discover_scene_dataset(args.config, args.output))
        return 0
    if args.command == 'qualify-scene-datasets':
        from .workflows.scene_datasets import qualify_scene_discovery
        print(qualify_scene_discovery(args.discovery, args.output))
        return 0
    if args.command == 'build-scene-datasets':
        from .workflows.scene_datasets import build_scene_dataset
        path = build_scene_dataset(args.config, args.discovery, args.output_dir, args.dataset_id,
                                   args.gpu_ids, args.workers, args.resume, args.allow_partial_targets)
        print(path)
        return 0 if read_json(path/'manifest.json')['status'] == 'sealed' else 1
    if args.command == 'scene-dataset-unit':
        from .workflows.scene_datasets import run_scene_dataset_unit
        return 0 if run_scene_dataset_unit(args.dataset, args.unit_index)['status'] == 'completed' else 1
    if args.command == 'augment-obstacles':
        from .construction.obstacle_config import load_obstacle_config
        from .workflows.obstacle_augmentation import augment_obstacles
        path = augment_obstacles(load_obstacle_config(args.config), args.output_dir, run_id=args.run_id)
        print(path)
        return 0 if read_json(path/'summary.json')['status'] == 'completed' else 1
    if args.command == 'export-molmobot':
        from .integrations.molmobot_dataset import export_molmobot
        try:
            print(export_molmobot(args.attempt, args.output,
                state_images_only=args.state_images_only,
                gripper_map=read_json(args.gripper_map) if args.gripper_map else None,
                seed=args.seed, chunk_length=args.chunk_length, conditioning_point=args.conditioning_point))
        except (ValueError, OSError) as exc:
            print('export-molmobot: ' + str(exc))
            return 1
        return 0
    if args.command == 'no-edit-status':
        import json
        return 0
    if args.command == 'collect-lastmile-datasets':
        from .workflows.lastmile_datasets import collect
        path=collect(args.dataset,args.assets_dir,args.config,args.output_dir,run_id=args.run_id,
                     gpu_ids=args.gpu_ids,workers=args.workers,samples=args.samples,resume=args.resume,
                     calibration_only=args.calibration_only,preflight_only=args.preflight_only,
                     max_trials=args.max_trials,export_state_images=not args.no_molmobot_export)
        print(path)
        return 0 if read_json(path/'summary.json')['status'] in ('completed','calibration_complete','preflight_complete') else 1
    if args.command == 'collect-no-edit':
        from .stations.no_edit_config import load_no_edit_config
        from .workflows.no_edit import collect_batch
        config=load_no_edit_config(args.config)
        if args.spacing_m is not None: config=replace(config,spacing_m=args.spacing_m)
        if args.trials_per_station is not None: config=replace(config,trials_per_station=args.trials_per_station)
        for key in ('max_tasks','max_trials'):
            if getattr(args,key) is not None and getattr(args,key)<=0: raise ValueError('invalid '+key)
        if any(g<0 for g in args.gpu_ids) or len(set(args.gpu_ids))!=len(args.gpu_ids): raise ValueError('invalid GPU list')
        assets=args.assets_dir.resolve()
        robot=load_config(RobotConfig,args.robot_config) if args.robot_config else RobotConfig(
            str(assets/'robots/rby1m/rby1_v1.2_site_control.xml'))
        if config.operation_backend=='molmobot':
            if (config.width,config.height)!=(1024,576):
                raise ValueError('MolmoBot requires actual 1024x576 rendering')
            robot=replace(robot,control_profile='molmobot-native-v1',control_hz=10.,physics_dt=.004)
        collection=load_config(CollectionConfig, args.collection_config) if args.collection_config else CollectionConfig(
            output_dir=str(args.output_dir.resolve()),seed=config.seed,
            width=config.width,height=config.height,record_video=not args.no_video)
        path=collect_batch(args.dataset_dir or assets/'scenes/procthor-10k-val',assets,robot,config,collection,
            run_id=args.run_id,gpu_ids=args.gpu_ids,max_workers=args.workers,houses=args.houses,resume=args.resume,
            index_only=args.index_only,targets=args.targets,max_tasks=args.max_tasks,max_trials=args.max_trials)
        print(path)
        return 0 if read_json(path/'summary.json')['status'] in ('completed','indexed_only') else 1


if __name__ == "__main__":
    raise SystemExit(main())
