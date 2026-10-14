"""保留当前命令名；参数和配置只在入口处理。"""
import argparse
from pathlib import Path

from .config import CollectionConfig
from .io import read_json

ROOT = Path(__file__).resolve().parents[2]


def parser():
    cli = argparse.ArgumentParser(prog='lastmile-dataflow')
    commands = cli.add_subparsers(dest='command', required=True)
    for name in ('discover-scene-datasets', 'qualify-scene-datasets'):
        command = commands.add_parser(name)
        command.add_argument('--config' if name.startswith('discover') else '--discovery', required=True, type=Path)
        command.add_argument('--output', required=True, type=Path)
    build = commands.add_parser('build-scene-datasets')
    build.add_argument('--config', required=True, type=Path)
    build.add_argument('--discovery', required=True, type=Path)
    build.add_argument('--dataset-id', required=True)
    build.add_argument('--allow-partial-targets', action='store_true')
    obstacles = commands.add_parser('augment-obstacles')
    obstacles.add_argument('--config', required=True, type=Path)
    obstacles.add_argument('--run-id', required=True)
    for name in ('collect-no-edit', 'collect-lastmile-datasets'):
        command = commands.add_parser(name)
        command.add_argument('--dataset-dir' if name == 'collect-no-edit' else '--dataset', required=True, type=Path)
        command.add_argument('--assets-dir', required=True, type=Path)
        command.add_argument('--config', type=Path, default=ROOT / ('configs/no_edit/curobo_v2.json' if name == 'collect-no-edit' else 'configs/lastmile_datasets/v0.json'))
        command.add_argument('--run-id', required=True)
        command.add_argument('--samples', nargs='+')
        command.add_argument('--max-trials', type=int)
        command.add_argument('--record-video', action='store_true')
        command.add_argument('--record-depth', action='store_true')
        command.add_argument('--record-physics', action='store_true')
        if name == 'collect-lastmile-datasets':
            command.add_argument('--calibration-only', action='store_true')
            command.add_argument('--calibration', type=Path)
    bench_build = commands.add_parser('build-lastmilebench')
    bench_build.add_argument('--dataset', required=True, type=Path)
    bench_build.add_argument('--output', required=True, type=Path)
    bench_build.add_argument('--calibration-dir', type=Path)
    bench_build.add_argument('--allow-uncalibrated-smoke', action='store_true')
    bench_build.add_argument('--starts-per-sample', type=int, default=1)
    bench_build.add_argument('--seed', type=int, default=0)
    bench_build.add_argument('--horizon', type=int, default=400)
    bench_build.add_argument('--wall-timeout-s', type=float, default=600.)
    bench_build.add_argument('--radius-m', type=float, default=1.5)
    bench_eval = commands.add_parser('evaluate-lastmilebench')
    bench_eval.add_argument('--benchmark', required=True, type=Path)
    bench_eval.add_argument('--output', required=True, type=Path)
    bench_eval.add_argument('--log-dir', type=Path, default=ROOT / 'outputs/logs')
    for command in (bench_build, bench_eval):
        command.add_argument('--policy-config', type=Path, default=ROOT / 'configs/policies/molmobot.json')
    for command in (build, obstacles, commands.choices['collect-no-edit'], commands.choices['collect-lastmile-datasets']):
        command.add_argument('--output-dir', type=Path, default=ROOT / 'outputs')
    for command in (build, commands.choices['collect-no-edit'], commands.choices['collect-lastmile-datasets'], bench_build, bench_eval):
        command.add_argument('--gpu-ids', nargs='+', type=int, required=True)
    export = commands.add_parser('export-molmobot')
    export.add_argument('--attempt', required=True, type=Path)
    export.add_argument('--output', required=True, type=Path)
    export.add_argument('--state-images-only', action='store_true')
    export.add_argument('--gripper-map', type=Path)
    export.add_argument('--seed', type=int, default=0)
    export.add_argument('--chunk-length', type=int, default=16)
    export.add_argument('--conditioning-point', nargs=2, type=float)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    if hasattr(args, 'gpu_ids') and (len(args.gpu_ids) != 1 or args.gpu_ids[0] < 0):
        raise ValueError('this implementation uses one explicitly selected GPU')
    if args.command == 'discover-scene-datasets':
        from .workflows.scene_datasets import discover_scene_dataset
        output = discover_scene_dataset(args.config, args.output)
    elif args.command == 'qualify-scene-datasets':
        from .workflows.scene_datasets import qualify_scene_discovery
        output = qualify_scene_discovery(args.discovery, args.output)
    elif args.command == 'build-scene-datasets':
        from .workflows.scene_datasets import build_scene_dataset
        output = build_scene_dataset(args.config, args.discovery, args.output_dir, args.dataset_id,
                                     args.gpu_ids, allow_partial_targets=args.allow_partial_targets)
    elif args.command == 'augment-obstacles':
        from .construction.obstacle_config import load_obstacle_config
        from .workflows.obstacle_augmentation import augment_obstacles
        output = augment_obstacles(load_obstacle_config(args.config), args.output_dir, run_id=args.run_id)
    elif args.command in ('collect-no-edit', 'collect-lastmile-datasets'):
        collection = CollectionConfig(record_video=args.record_video, record_depth=args.record_depth,
                                      record_physics=args.record_physics)
        if args.command == 'collect-lastmile-datasets':
            from .workflows.lastmile_datasets import collect
            output = collect(args.dataset, args.assets_dir, args.config, args.output_dir, run_id=args.run_id,
                gpu_ids=args.gpu_ids, samples=args.samples, calibration_only=args.calibration_only,
                calibration=args.calibration, max_trials=args.max_trials, collection=collection)
        else:
            from .stations.no_edit_config import load_no_edit_config
            from .workflows.no_edit import collect_scene_dataset
            config = load_no_edit_config(args.config)
            output = collect_scene_dataset(args.dataset_dir, args.assets_dir, config, collection, args.output_dir,
                run_id=args.run_id, gpu_ids=args.gpu_ids, samples=args.samples, max_trials=args.max_trials)
    elif args.command == 'build-lastmilebench':
        from .workflows.lastmilebench import build
        output = build(args.dataset, args.output, args.policy_config, gpu_ids=args.gpu_ids,
            starts_per_sample=args.starts_per_sample, calibration_dir=args.calibration_dir,
            allow_uncalibrated=args.allow_uncalibrated_smoke, seed=args.seed, horizon=args.horizon,
            wall_timeout_s=args.wall_timeout_s, radius_m=args.radius_m)
    elif args.command == 'evaluate-lastmilebench':
        from .workflows.lastmilebench import evaluate
        output = evaluate(args.benchmark, args.output, args.policy_config, gpu_ids=args.gpu_ids, log_dir=args.log_dir)
    else:
        from .integrations.molmobot_dataset import export_molmobot
        output = export_molmobot(args.attempt, args.output, state_images_only=args.state_images_only,
            gripper_map=read_json(args.gripper_map) if args.gripper_map else None, seed=args.seed,
            chunk_length=args.chunk_length, conditioning_point=args.conditioning_point)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
