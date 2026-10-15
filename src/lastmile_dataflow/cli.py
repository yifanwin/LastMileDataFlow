"""Five experiment commands; configuration and workflows own the real work."""
import argparse
from pathlib import Path


def parser():
    cli = argparse.ArgumentParser(prog='lastmile-dataflow')
    commands = cli.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build-scenes')
    build.add_argument('--config', required=True, type=Path)
    build.add_argument('--allow-partial-targets', action='store_true')
    for name in ('evaluate-stations', 'collect-lastmile'):
        command = commands.add_parser(name)
        command.add_argument('--dataset', required=True, type=Path)
        command.add_argument('--assets-dir', required=True, type=Path)
        command.add_argument('--config', type=Path, default=Path('configs/execution/curobo_v2.json'))
        command.add_argument('--samples', nargs='+')
        command.add_argument('--max-trials', type=int)
        command.add_argument('--record-video', action='store_true')
        command.add_argument('--record-depth', action='store_true')
        command.add_argument('--record-physics', action='store_true')
        if name == 'collect-lastmile':
            command.add_argument('--calibration', type=Path)
    bench = commands.add_parser('build-bench')
    bench.add_argument('--dataset', required=True, type=Path)
    bench.add_argument('--calibration', type=Path)
    bench.add_argument('--allow-uncalibrated-smoke', action='store_true')
    bench.add_argument('--starts-per-sample', type=int, default=1)
    bench.add_argument('--seed', type=int, default=0)
    bench.add_argument('--horizon', type=int, default=400)
    bench.add_argument('--wall-timeout-s', type=float, default=600.)
    bench.add_argument('--radius-m', type=float, default=1.5)
    evaluate = commands.add_parser('evaluate-bench')
    evaluate.add_argument('--benchmark', required=True, type=Path)
    for command in (bench, evaluate):
        command.add_argument('--output', required=True, type=Path)
        command.add_argument('--policy-config', required=True, type=Path)
    for command in (build, commands.choices['evaluate-stations'], commands.choices['collect-lastmile']):
        command.add_argument('--output-dir', type=Path, default=Path('outputs'))
        command.add_argument('--run-id', required=True)
    for command in commands.choices.values():
        command.add_argument('--gpu', required=True, type=int)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    gpu_ids = [args.gpu]
    import os
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu), MUJOCO_GL='egl')
    if args.command == 'build-scenes':
        from .workflows.scene_datasets import build_scenes
        output = build_scenes(args.config, args.output_dir, args.run_id, gpu_ids=gpu_ids,
                              allow_partial_targets=args.allow_partial_targets)
    elif args.command in ('evaluate-stations', 'collect-lastmile'):
        from .config import CollectionConfig
        from .runtime.execution_config import load_execution_config
        from .workflows.collection import evaluate_stations, collect_lastmile
        config = load_execution_config(args.config)
        recording = CollectionConfig(record_video=args.record_video, record_depth=args.record_depth,
                                     record_physics=args.record_physics)
        options = dict(run_id=args.run_id, gpu_ids=gpu_ids, samples=args.samples, max_trials=args.max_trials)
        if args.command == 'collect-lastmile':
            output = collect_lastmile(args.dataset, args.assets_dir, config, recording, args.output_dir,
                                      calibration=args.calibration, **options)
        else:
            output = evaluate_stations(args.dataset, args.assets_dir, config, recording, args.output_dir, **options)
    elif args.command == 'build-bench':
        from .workflows.lastmilebench import build
        output = build(args.dataset, args.output, args.policy_config, gpu_ids=gpu_ids,
            starts_per_sample=args.starts_per_sample, calibration_dir=args.calibration,
            allow_uncalibrated=args.allow_uncalibrated_smoke, seed=args.seed, horizon=args.horizon,
            wall_timeout_s=args.wall_timeout_s, radius_m=args.radius_m)
    else:
        from .workflows.lastmilebench import evaluate
        output = evaluate(args.benchmark, args.output, args.policy_config, gpu_ids=gpu_ids,
                          log_dir=Path('outputs/logs'))
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
