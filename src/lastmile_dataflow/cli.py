"""Five experiment commands; configuration and workflows own the real work."""
import logging
import argparse
from pathlib import Path

from .config import load_local_config
from .utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def parser():
    cli = argparse.ArgumentParser(prog='lastmile-dataflow')
    try:
        local = load_local_config()
    except (OSError, ValueError, TypeError) as error:
        cli.error(f'configs/local.json: {error}')
    cli.set_defaults(log_dir=local.output_dir / 'logs')
    commands = cli.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build-scenes')
    source = build.add_mutually_exclusive_group(required=True)
    source.add_argument('--config', type=Path)
    source.add_argument('--preview-dataset', type=Path, help='只补生成已有场景库的组合图和浏览页')
    build.add_argument('--allow-partial-targets', action='store_true')
    build.add_argument('--build-workers', type=int, default=1, help='房屋发现和任务构建进程数（1–12）')
    for name in ('evaluate-stations', 'collect-lastmile'):
        command = commands.add_parser(name)
        if name == 'collect-lastmile':
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument('--dataset', type=Path)
            source.add_argument('--materialize-from', type=Path, help='仅物化已有引用型数据集；无需 GPU 或资产')
        else:
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument('--dataset', type=Path)
            source.add_argument('--replay-from', type=Path, help='按实测状态离线渲染已有标定 run')
            command.add_argument('--rollouts', nargs='+', help='离线回放的 rollout ID')
            command.add_argument('--replay-camera', help='recording.json 中的相机别名')
        command.add_argument('--assets-dir', type=Path,
                             default=local.assets_dir, help='资产目录；默认读取 configs/local.json')
        command.add_argument('--config', type=Path, default=Path('configs/execution/curobo_v2.json'))
        command.add_argument('--samples', nargs='+')
        command.add_argument('--max-trials', type=int)
        command.add_argument('--record-video', action='store_true')
        command.add_argument('--record-depth', action='store_true')
        command.add_argument('--record-physics', action='store_true')
        command.add_argument('--station-workers', type=int, default=1, help='cuRobo 标定的站位进程数')
        command.add_argument('--cpu-workers', type=int, default=60, help='独立 trial 执行容量（1–60）')
        command.add_argument('--preparation-workers', type=int, default=12, help='站位准备并发（1–12）')
        command.add_argument('--recording-mode', choices=('numerical', 'visual'),
                             help='Panda 标定默认 numerical；显式图像/视频记录使用 visual')
        command.add_argument('--egl-devices', nargs='+', type=int, help='与 --gpu 对应的 EGL 设备索引')
        command.add_argument('--no-planner-reuse', action='store_true', help='关闭同站位闭爪前 planner 复用')
        if name == 'collect-lastmile':
            command.add_argument('--calibration', type=Path)
    bench = commands.add_parser('build-bench')
    bench.add_argument('--dataset', required=True, type=Path)
    bench.add_argument('--calibration', required=True, type=Path)
    bench.add_argument('--config', type=Path, default=Path('configs/benchmark/v0.1.json'))
    evaluate = commands.add_parser('evaluate-bench')
    evaluate.add_argument('--benchmark', required=True, type=Path)
    evaluate.add_argument('--policy-config', required=True, type=Path)
    for command in (bench, evaluate):
        command.add_argument('--output', required=True, type=Path)
    for command in (build, commands.choices['evaluate-stations'], commands.choices['collect-lastmile']):
        command.add_argument('--output-dir', type=Path, default=local.output_dir,
                             help='输出根目录；默认读取 configs/local.json，否则为 outputs')
        command.add_argument('--run-id', required=command is not build)
    for name, command in commands.choices.items():
        command.add_argument('--gpu', required=name not in ('collect-lastmile', 'build-bench'), type=int,
                             **({'nargs': '+'} if name in ('evaluate-stations', 'collect-lastmile') else {}))
    return cli


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.command == 'build-scenes' and args.config and not args.run_id:
        cli.error('build-scenes --config requires --run-id')
    materializing = args.command == 'collect-lastmile' and args.materialize_from is not None
    replaying = args.command == 'evaluate-stations' and args.replay_from is not None
    if replaying and (not args.rollouts or not args.replay_camera):
        cli.error('--replay-from requires --rollouts and --replay-camera')
    if args.command == 'evaluate-stations' and not replaying and args.assets_dir is None:
        cli.error('evaluate-stations --dataset requires --assets-dir (or local assets_dir)')
    if args.command == 'collect-lastmile' and not materializing and (args.gpu is None or args.assets_dir is None):
        cli.error('collect-lastmile --dataset requires --gpu and --assets-dir (or local assets_dir)')
    setup_logging()
    logger.info('开始命令=%s GPU=%s', args.command, 'CPU' if materializing or args.gpu is None else args.gpu)
    gpu_ids = args.gpu if isinstance(args.gpu, list) else [args.gpu]
    import os
    if not materializing and args.gpu is not None:
        egl_devices = getattr(args, 'egl_devices', None)
        os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu_ids[0]),
                          MUJOCO_EGL_DEVICE_ID=str(egl_devices[0] if egl_devices else gpu_ids[0]))
        os.environ.setdefault('MUJOCO_GL', 'egl')
    if replaying:
        from .data.rendering.replay import render_run
        output = render_run(args.replay_from, args.output_dir / 'replays' / args.run_id,
                            rollouts=args.rollouts, camera=args.replay_camera)
    elif materializing:
        from .data.export.lastmile_dataset import materialize_lastmile
        output = materialize_lastmile(args.materialize_from, args.output_dir / args.run_id)
    elif args.command == 'build-scenes' and args.preview_dataset:
        from .data.visualization.scene_preview import export_scene_previews
        output = export_scene_previews(args.preview_dataset)
    elif args.command == 'build-scenes':
        from .workflows.scene_datasets import build_scenes
        output = build_scenes(args.config, args.output_dir, args.run_id, gpu_ids=gpu_ids,
                              allow_partial_targets=args.allow_partial_targets, workers=args.build_workers)
    elif args.command in ('evaluate-stations', 'collect-lastmile'):
        from .config import CollectionConfig
        from .runtime.execution_config import load_execution_config
        from .workflows.collection import evaluate_stations, collect_lastmile
        config = load_execution_config(args.config)
        recording = CollectionConfig(record_video=args.record_video, record_depth=args.record_depth,
                                     record_physics=args.record_physics,
                                     record_images=(args.command == 'collect-lastmile'
                                                    or not config.shared_station_preparation or args.record_depth))
        options = dict(run_id=args.run_id, gpu_ids=gpu_ids, samples=args.samples, max_trials=args.max_trials,
                       station_workers=args.station_workers, egl_devices=args.egl_devices,
                       reuse_planners=not args.no_planner_reuse,
                       cpu_workers=args.cpu_workers, preparation_workers=args.preparation_workers,
                       recording_mode=args.recording_mode or ('visual' if args.record_video or args.record_depth else 'numerical'))
        if args.command == 'collect-lastmile':
            output = collect_lastmile(args.dataset, args.assets_dir, config, recording, args.output_dir,
                                      calibration=args.calibration, **options)
        else:
            output = evaluate_stations(args.dataset, args.assets_dir, config, recording, args.output_dir, **options)
    elif args.command == 'build-bench':
        from .workflows.lastmilebench import build
        output = build(args.dataset, args.output, calibration_dir=args.calibration,
                       config_path=args.config, gpu_ids=gpu_ids)
    else:
        from .workflows.lastmilebench import evaluate
        output = evaluate(args.benchmark, args.output, args.policy_config, gpu_ids=gpu_ids,
                          log_dir=args.log_dir)
    logger.info('命令结束=%s 输出=%s（任务结果以主记录为准）', args.command, output)
    # stdout 只保留结果目录，进度日志写 stderr。
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
