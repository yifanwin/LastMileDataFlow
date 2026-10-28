"""Incrementally render S0/S1 videos for completed valid scenes, optionally in parallel."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from lastmile_dataflow.data.evaluation.failure_analysis import valid_scene_replays


ROOT = Path(__file__).resolve().parents[1]


def pipeline_env():
    env = os.environ.copy()
    prefix = '/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64'
    env['LD_LIBRARY_PATH'] = prefix + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    env.setdefault('CUDA_HOME', '/data1/wenyifan/opt/cuda-12.8')
    env['MUJOCO_GL'] = 'egl'
    env.setdefault('MPLCONFIGDIR', '/tmp/lastmile-mpl')
    env['PYTHONPATH'] = str(ROOT / 'src') + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


def pick_gpu():
    try:
        out = subprocess.check_output(['nvidia-smi', '--query-gpu=index,utilization.gpu',
                                       '--format=csv,noheader,nounits'], text=True)
        rows = []
        for line in out.strip().splitlines():
            index, util = [item.strip() for item in line.split(',')]
            rows.append((float(util), int(index)))
        return min(rows)[1]
    except Exception:
        return 0


def load_rendered(path):
    if path.is_file():
        return set(json.loads(path.read_text()).get('rendered', []))
    return set()


def save_rendered(path, rendered):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'rendered': sorted(rendered)}, indent=2) + '\n')


def replay(run, rollouts, run_id, output_dir, gpu, env):
    command = [sys.executable, '-m', 'lastmile_dataflow.cli', 'evaluate-stations',
               '--replay-from', run, '--rollouts', *rollouts, '--replay-layout', 'grid',
               '--output-dir', output_dir, '--run-id', run_id, '--gpu', gpu]
    print(json.dumps({'run': str(run), 'rollouts': len(rollouts), 'gpu': gpu,
                      'replay_run': run_id}), flush=True)
    return subprocess.run([str(item) for item in command], check=False, env=env).returncode == 0


def build_jobs(runs, rendered, batch_size, gpus):
    jobs = []
    for run in runs:
        rows = valid_scene_replays([run])
        todo = [row for row in rows
                if f"{row['calibration']}|{row['rollout_id']}" not in rendered]
        if not todo:
            continue
        rollouts = list(dict.fromkeys(row['rollout_id'] for row in todo))
        for start in range(0, len(rollouts), batch_size):
            chunk = rollouts[start:start + batch_size]
            selected = set(chunk)
            jobs.append({'run': run, 'rollouts': chunk,
                         'rows': [row for row in todo if row['rollout_id'] in selected],
                         'gpu': gpus[len(jobs) % len(gpus)] if gpus else None,
                         'index': len(jobs)})
    return jobs


def render_job(job, output_dir, env, stamp, group_by_run=False):
    run, rollouts = job['run'], job['rollouts']
    gpu = job['gpu'] if job['gpu'] is not None else pick_gpu()
    run_id = f"valid-{Path(run).name}-{stamp}-g{gpu}-b{job['index']:03d}"
    if group_by_run:
        # Replays land in replays/<run>/<batch> instead of one flat directory.
        run_id = f'{Path(run).name}/{run_id}'
    if replay(run, rollouts, run_id, output_dir, gpu, env):
        return {rollout: run_id for rollout in rollouts}
    # One bad rollout must not hide the rest of the batch.
    done = {}
    for offset, rollout in enumerate(rollouts):
        single = f'{run_id}-r{offset:03d}'
        if replay(run, [rollout], single, output_dir, gpu, env):
            done[rollout] = single
    return done


def record(manifest_path, rows, done, rendered, state_path):
    rendered.update(f"{row['calibration']}|{row['rollout_id']}"
                    for row in rows if row['rollout_id'] in done)
    save_rendered(state_path, rendered)
    with manifest_path.open('a') as stream:
        for row in rows:
            stream.write(json.dumps({**row, 'rendered': row['rollout_id'] in done,
                                     'replay_run': done.get(row['rollout_id'])}) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='增量渲染有效场景 S0/S1 视频')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--gpu', type=int)
    parser.add_argument('--gpus', nargs='+', type=int,
                        help='并行渲染使用的 GPU 列表；省略时按 run 顺序单卡渲染')
    parser.add_argument('--batch-size', type=int, default=8,
                        help='每个并行任务的 rollout 数')
    parser.add_argument('--runs', nargs='+', type=Path)
    parser.add_argument('--max-runs', type=int)
    parser.add_argument('--group-by-run', action='store_true',
                        help='回放写到 replays/<标定 run>/ 下，而不是平铺在 replays/')
    args = parser.parse_args(argv)
    state = json.loads(args.state.read_text())
    runs = args.runs or [args.output_dir / item for item in state.get('completed', [])]
    if args.max_runs:
        runs = runs[:args.max_runs]
    state_path = args.output_dir / 'replays' / 'incremental_valid_state.json'
    manifest_path = args.output_dir / 'replays' / 'incremental_valid_manifest.jsonl'
    rendered = load_rendered(state_path)
    env = pipeline_env()
    total = 0
    jobs = build_jobs(runs, rendered, args.batch_size, args.gpus) if args.gpus else []
    if jobs:
        stamp = int(time.time())
        with ThreadPoolExecutor(max_workers=min(len(args.gpus), len(jobs))) as pool:
            futures = {pool.submit(render_job, job, args.output_dir, env, stamp, args.group_by_run): job
                       for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                done = future.result()
                record(manifest_path, job['rows'], done, rendered, state_path)
                total += len(done)
                print(json.dumps({'batch': job['index'], 'gpu': job['gpu'],
                                  'rendered': len(done), 'of': len(job['rollouts'])}),
                      flush=True)
    else:
        for run in runs:
            rows = valid_scene_replays([run])
            todo = [row for row in rows
                    if f"{row['calibration']}|{row['rollout_id']}" not in rendered]
            if not todo:
                continue
            rollouts = list(dict.fromkeys(row['rollout_id'] for row in todo))
            gpu = args.gpu if args.gpu is not None else pick_gpu()
            done = render_job({'run': run, 'rollouts': rollouts, 'gpu': gpu, 'index': 0},
                              args.output_dir, env, int(time.time()), args.group_by_run)
            record(manifest_path, todo, done, rendered, state_path)
            total += len(done)
    print(json.dumps({'rendered_this_call': total, 'rendered_total': len(rendered),
                      'state': str(state_path), 'manifest': str(manifest_path)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
