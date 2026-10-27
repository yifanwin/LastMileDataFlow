"""Render representative rollouts from one or more no-edit calibration runs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def read_manifest(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def renderable(calibration, rollout):
    path = Path(calibration) / 'rollouts' / rollout / 'recording.json'
    try:
        metadata = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return metadata.get('steps', 0) > 0 and bool(metadata.get('complete'))


def replay(calibration, rollouts, run_id, output_dir, gpu, env):
    command = [sys.executable, '-m', 'lastmile_dataflow.cli', 'evaluate-stations',
               '--replay-from', calibration, '--rollouts', *rollouts,
               '--replay-layout', 'grid', '--output-dir', output_dir,
               '--run-id', run_id, '--gpu', str(gpu)]
    print(json.dumps({'run_id': run_id, 'calibration': calibration, 'rollouts': len(rollouts)}),
          flush=True)
    return subprocess.run([str(item) for item in command], check=False, env=env).returncode == 0


def pipeline_env():
    env = os.environ.copy()
    prefix = '/data1/wenyifan/opt/lib:/data1/wenyifan/opt/cuda-12.8/lib64'
    env['LD_LIBRARY_PATH'] = prefix + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    env.setdefault('CUDA_HOME', '/data1/wenyifan/opt/cuda-12.8')
    env['MUJOCO_GL'] = 'egl'
    env['PYTHONPATH'] = str(ROOT / 'src') + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    return env


def main(argv=None):
    parser = argparse.ArgumentParser(description='渲染无编辑标定代表视频')
    parser.add_argument('--manifests', nargs='+', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--run-id-prefix', required=True)
    parser.add_argument('--gpu', type=int, default=0)
    args = parser.parse_args(argv)
    output = args.output_dir / 'replays'
    output.mkdir(parents=True, exist_ok=True)
    combined_path = output / f'{args.run_id_prefix}-manifest.jsonl'
    combined = []
    env = pipeline_env()
    groups = {}
    for manifest in args.manifests:
        for row in read_manifest(manifest):
            if not row.get('rollout_id') or not row.get('calibration'):
                continue
            groups.setdefault(row['calibration'], []).append(row)
    for index, (calibration, rows) in enumerate(sorted(groups.items())):
        usable = [row for row in rows
                  if row.get('rollout_id') and renderable(calibration, str(row['rollout_id']))]
        skipped = [row for row in rows if row not in usable]
        rollouts = list(dict.fromkeys(str(row['rollout_id']) for row in usable))
        rendered = {}
        if rollouts:
            run_id = f'{args.run_id_prefix}-{index:03d}'
            if replay(calibration, rollouts, run_id, args.output_dir, args.gpu, env):
                rendered = {rollout: run_id for rollout in rollouts}
            else:
                # One bad rollout must not hide the videos that can be rendered.
                for offset, rollout in enumerate(rollouts):
                    single = f'{run_id}-r{offset:03d}'
                    if replay(calibration, [rollout], single, args.output_dir, args.gpu, env):
                        rendered[rollout] = single
        for row in usable:
            if row['rollout_id'] in rendered:
                combined.append({**row, 'rendered': True, 'replay_run': rendered[row['rollout_id']]})
            else:
                combined.append({**row, 'rendered': False, 'skip_reason': 'replay_failed'})
        for row in skipped:
            combined.append({**row, 'rendered': False,
                             'skip_reason': 'no_executed_steps_or_incomplete_recording'})
    combined_path.write_text(''.join(json.dumps(row) + '\n' for row in combined))
    print(combined_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
