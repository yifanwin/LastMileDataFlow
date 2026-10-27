"""Overlapped no-edit pipeline: prepare later houses while earlier houses calibrate."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from lastmile_dataflow.data.evaluation.failure_analysis import target_stats


ROOT = Path(__file__).resolve().parents[1]


def merge_category_stats(state, stats):
    for row in stats.get('target_categories', []):
        key = f"{row['category']}|{row['operation']}"
        item = state.setdefault('category_stats', {}).setdefault(key, {
            'category': row['category'], 'operation': row['operation'],
            'tasks': 0, 'house_ids': [], 'stations': 0, 'trials': 0, 'successes': 0,
            'eligible': 0, 'valid': 0, 'all_fail': 0, 'all_success': 0})
        for field in ('tasks', 'stations', 'trials', 'successes', 'eligible', 'valid', 'all_fail', 'all_success'):
            item[field] += int(row.get(field, 0))
        item['house_ids'] = sorted(set(item['house_ids']) | set(row.get('house_ids', [])))
    return state


def exclusion_from_categories(state, min_trials, min_success_rate=0.001):
    manual = set(state.get('manual_exclude_categories', []))
    permanent = set(state.get('permanent_categories', []))
    computed = {item['category'] for item in state.get('category_stats', {}).values()
                if item['trials'] >= min_trials
                and item['successes'] / item['trials'] < min_success_rate}
    return sorted((computed | manual) - permanent)


def load_state(path):
    if path.is_file():
        return json.loads(path.read_text())
    return {'completed': [], 'exclude_categories': [], 'manual_exclude_categories': [],
            'permanent_categories': [], 'chunks': {}}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + '\n')


def chunks_of(houses, first, rest):
    chunks, index = [], 0
    if first and len(houses) > first:
        chunks.append(houses[:first])
        index = first
    while index < len(houses):
        chunks.append(houses[index:index + rest])
        index += rest
    return chunks


def pipeline_env():
    env = os.environ.copy()
    prefix = '/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64'
    env['LD_LIBRARY_PATH'] = prefix + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    env.setdefault('CUDA_HOME', '/data1/wenyifan/opt/cuda-12.8')
    env['MUJOCO_GL'] = 'egl'
    env.setdefault('MPLCONFIGDIR', '/tmp/lastmile-mpl')
    env['PYTHONPATH'] = str(ROOT / 'src') + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


def start(command, log_path, env):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open('a')
    log.write(f"\n$ {' '.join(map(str, command))}\n")
    log.flush()
    return subprocess.Popen([str(item) for item in command], stdout=log, stderr=subprocess.STDOUT, env=env)


def wait(process, label):
    code = process.wait()
    if code:
        raise RuntimeError(f'{label} failed with exit code {code}')
    return code


def recipe_for(base, houses, exclusions, path):
    value = {**base, 'houses': houses, 'exclude_categories': sorted(exclusions)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description='无编辑直接标定流水线')
    parser.add_argument('--recipe', required=True, type=Path, help='含完整 house 列表的 v2 配方')
    parser.add_argument('--run-prefix', required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--assets-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path('configs/execution/panda_omron_no_edit_curobo.json'))
    parser.add_argument('--gpu', nargs='+', type=int, default=list(range(8)))
    parser.add_argument('--first-chunk-houses', type=int, default=5)
    parser.add_argument('--chunk-houses', type=int, default=25)
    parser.add_argument('--build-workers', type=int, default=32)
    parser.add_argument('--cpu-workers', type=int, default=128)
    parser.add_argument('--preparation-workers', type=int, default=32)
    parser.add_argument('--station-workers', type=int, default=8)
    parser.add_argument('--planning-workers-per-gpu', type=int, nargs='+', default=[1])
    parser.add_argument('--min-trials', type=int, default=30, help='仅用于 target_stats 兼容参数')
    parser.add_argument('--min-houses', type=int, default=2, help='仅用于 target_stats 兼容参数')
    parser.add_argument('--min-streak', type=int, default=1000,
                        help='累计 trial 达到该数量后才允许按成功率剔除')
    parser.add_argument('--min-success-rate', type=float, default=0.001,
                        help='累计成功率低于该值时排除类别')
    parser.add_argument('--heatmap-workers', type=int, default=16)
    parser.add_argument('--skip-heatmaps', action='store_true')
    parser.add_argument('--max-chunks', type=int)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args(argv)

    base = json.loads(args.recipe.read_text())
    houses = list(base['houses'])
    output = args.output_dir
    state_path = args.state or (output / '_calibration_inputs' / f'{args.run_prefix}-state.json')
    state = load_state(state_path)
    analysis_root = output / '_analysis' / args.run_prefix
    env = pipeline_env()
    if state.get('completed'):
        if not state.get('category_stats'):
            initial = target_stats([output / item for item in state['completed']],
                                   min_trials=args.min_trials, min_houses=args.min_houses)
            merge_category_stats(state, initial)
        state['exclude_categories'] = exclusion_from_categories(state, args.min_streak, args.min_success_rate)
        save_state(state_path, state)
    chunks = chunks_of(houses, args.first_chunk_houses, args.chunk_houses)
    if args.max_chunks:
        chunks = chunks[:args.max_chunks]
    recipe_dir = output / '_calibration_inputs' / 'recipes'
    log_dir = output / 'logs'
    prepared = {}
    heatmaps = []
    print(f'chunks={len(chunks)} run_prefix={args.run_prefix} state={state_path}', flush=True)

    def prepare_command(index, chunk):
        run_id = f'{args.run_prefix}-c{index:03d}'
        input_root = output / '_calibration_inputs' / run_id
        recipe = recipe_for(base, chunk, state['exclude_categories'],
                            recipe_dir / f'{args.run_prefix}-c{index:03d}.json')
        command = [sys.executable, ROOT / 'scripts/prepare_no_edit_inputs.py',
                   '--recipe', recipe, '--output', input_root,
                   '--build-workers', args.build_workers, '--gpu', *args.gpu]
        return run_id, input_root, command

    def calibration_command(run_id, input_root):
        return [sys.executable, '-m', 'lastmile_dataflow.cli', 'evaluate-stations',
                '--dataset', input_root, '--assets-dir', args.assets_dir,
                '--config', args.config, '--output-dir', output, '--run-id', run_id,
                '--gpu', *args.gpu, '--egl-devices', *args.gpu,
                '--cpu-workers', args.cpu_workers,
                '--preparation-workers', args.preparation_workers,
                '--station-workers', args.station_workers,
                '--planning-workers-per-gpu', *args.planning_workers_per_gpu,
                '--recording-mode', 'numerical']

    # First chunk is prepared before calibration starts; later chunks overlap with calibration.
    first_id, first_input, first_command = prepare_command(0, chunks[0])
    if (first_input / 'scenes.jsonl').is_file():
        print(f'skip existing input {first_input}', flush=True)
    else:
        print(f'prepare {first_id} houses={chunks[0][0]}-{chunks[0][-1]}', flush=True)
        wait(start(first_command, log_dir / f'{first_id}-prepare.log', env), f'prepare {first_id}')
    prepared[0] = first_input

    for index, chunk in enumerate(chunks):
        run_id, input_root, _ = prepare_command(index, chunk)
        prepared[index] = input_root
        calibration = None
        if run_id not in state['completed']:
            print(f'calibrate {run_id} houses={chunk[0]}-{chunk[-1]}', flush=True)
            calibration = start(calibration_command(run_id, input_root),
                                log_dir / f'{run_id}.log', env)
        next_prepare = None
        if index + 1 < len(chunks):
            next_id, next_input, next_command = prepare_command(index + 1, chunks[index + 1])
            if (next_input / 'scenes.jsonl').is_file():
                print(f'skip existing input {next_input}', flush=True)
            else:
                print(f'prepare {next_id} while {run_id} calibrates', flush=True)
                next_prepare = start(next_command, log_dir / f'{next_id}-prepare.log', env)
        new_run = False
        if calibration is not None:
            try:
                wait(calibration, f'calibrate {run_id}')
            except Exception:
                if next_prepare is not None:
                    next_prepare.wait()
                raise
            state['completed'].append(run_id)
            save_state(state_path, state)
            new_run = True
        if (not args.skip_heatmaps and (output / run_id / 'trials.jsonl').is_file()
                and not (output / run_id / 'heatmap_manifest.jsonl').is_file()):
            print(f'heatmaps {run_id} while next chunk runs', flush=True)
            heatmaps.append(start([sys.executable, ROOT / 'scripts/export_no_edit_heatmaps.py',
                                   '--calibration', output / run_id,
                                   '--workers', args.heatmap_workers, '--skip-existing'],
                                  log_dir / f'{run_id}-heatmap.log', env))
        if next_prepare is not None:
            wait(next_prepare, f'prepare {next_id}')
        if new_run:
            stats = target_stats([output / run_id], min_trials=args.min_trials,
                                 min_houses=args.min_houses)
            merge_category_stats(state, stats)
            state['exclude_categories'] = exclusion_from_categories(state, args.min_streak, args.min_success_rate)
            save_state(state_path, state)
            analysis_root.mkdir(parents=True, exist_ok=True)
            (analysis_root / 'target_success_rates.json').write_text(json.dumps({
                'categories': list(state.get('category_stats', {}).values()),
                'exclude_categories': state['exclude_categories'],
            }, indent=2, default=str) + '\n')
            print(json.dumps({'runs': len(state['completed']), 'chunk_counts': stats['counts'],
                              'exclude_categories': state['exclude_categories']}, ensure_ascii=False),
                  flush=True)
    for process in heatmaps:
        wait(process, 'heatmap export')
    print(json.dumps({'state': str(state_path), 'analysis': str(analysis_root),
                      'completed': state['completed']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
