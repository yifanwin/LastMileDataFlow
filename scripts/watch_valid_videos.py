"""Render valid-scene videos for each calibration chunk as it completes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


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


def main(argv=None):
    parser = argparse.ArgumentParser(description='按 chunk 增量渲染有效场景视频')
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--driver-pid', required=True, type=int)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--gpus', nargs='+', type=int, default=list(range(8)))
    parser.add_argument('--interval', type=int, default=300)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--group-by-run', action='store_true',
                        help='标定 run 位于 runs/<组>/ 下；回放按 run 分子目录')
    args = parser.parse_args(argv)
    state_path = args.output_dir / 'replays' / 'valid_watch_state.json'
    if state_path.is_file():
        done = set(json.loads(state_path.read_text()).get('runs', []))
    else:
        # Everything already completed when the watcher starts has been rendered.
        done = set(json.loads(args.state.read_text()).get('completed', []))
        state_path.write_text(json.dumps({'runs': sorted(done)}, indent=2) + '\n')
    env = pipeline_env()
    while True:
        state = json.loads(args.state.read_text())
        pending = [item for item in state.get('completed', []) if item not in done]
        if pending:
            runs = [next(iter(sorted((args.output_dir / 'runs').glob(f'*/{item}'))), args.output_dir / item)
                    for item in pending]
            command = [sys.executable, str(ROOT / 'scripts/render_valid_videos.py'),
                       '--state', str(args.state), '--output-dir', str(args.output_dir),
                       '--gpus', *map(str, args.gpus), '--batch-size', str(args.batch_size),
                       '--runs', *map(str, runs), *(['--group-by-run'] if args.group_by_run else [])]
            print(json.dumps({'rendering': pending}), flush=True)
            code = subprocess.run(command, env=env).returncode
            if code == 0:
                done.update(pending)
                state_path.write_text(json.dumps({'runs': sorted(done)}, indent=2) + '\n')
            else:
                print(json.dumps({'failed': pending, 'code': code}), flush=True)
        driver_alive = os.path.exists(f'/proc/{args.driver_pid}')
        if not driver_alive and not pending:
            break
        time.sleep(args.interval)
    print(json.dumps({'watcher_done': True, 'runs': len(done)}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
