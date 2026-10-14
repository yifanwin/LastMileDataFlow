#!/usr/bin/env python3
"""Serialize `collect-no-edit` over a queue of per-sample NoEditConfig files.

Each config targets exactly one scene package (one house), so one `collect-no-edit`
invocation scores one sample. This driver keeps a fixed number of those
invocations alive at once, each on a distinct GPU, and records every attempt so a
partial batch is still auditable. It never edits a config or a run directory: the
run-id is chosen once and reused verbatim on `--resume`.

Usage:
  python scripts/run_scene_eval_queue.py --configs <dir> --tag <name> --workers 7
"""
import argparse, json, os, signal, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gpu_free(fallback_ids=()):
    """Query GPU state. `nvidia-smi` can stall for tens of seconds under heavy
    multi-job load, so a timeout falls back to "assume the requested GPUs are
    usable" instead of killing the whole queue."""
    try:
        out = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=index,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits'], text=True, timeout=60)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return [{'index': i, 'util': 0, 'free_mb': 10 ** 9, 'assumed': True} for i in fallback_ids]
    rows = []
    for line in out.splitlines():
        index, util, used, total = (int(v.strip()) for v in line.split(','))
        rows.append({'index': index, 'util': util, 'free_mb': total - used})
    return rows


def usable_gpus(ids, max_util, min_free_mb):
    rows = gpu_free(fallback_ids=ids)
    return [r['index'] for r in rows if r['index'] in ids
            and r['util'] <= max_util and r['free_mb'] >= min_free_mb]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--configs', required=True, nargs='+', type=Path,
                    help='one or more dirs with index.json; dispatched in the given order')
    ap.add_argument('--runner', default=None, type=Path,
                    help="run-id prefix plus log name; defaults to <basename of --tag>")
    ap.add_argument('--tag', required=True, help='run-id prefix; one <tag>-<sample_id> per sample')
    ap.add_argument('--workers', type=int, default=7)
    ap.add_argument('--gpu-ids', nargs='+', type=int, default=list(range(8)))
    ap.add_argument('--max-gpu-util', type=int, default=50)
    ap.add_argument('--min-free-mb', type=int, default=10000)
    ap.add_argument('--assets-dir', type=Path,
                    default=Path('/data0/wenyifan/MoMaTrajGen/molmospaces_data/assets'))
    ap.add_argument('--collection-config', type=Path,
                    default=ROOT / 'configs/collection/no_raw_first.json')
    ap.add_argument('--python', default=None, help='interpreter for bin/lastmile-dataflow')
    ap.add_argument('--poll-seconds', type=int, default=60)
    ap.add_argument('--config-key', default='config',
                    help='field in index.json holding the NoEditConfig path')
    ap.add_argument('--resume', action='store_true',
                    help='skip samples whose run already reached status=completed and pass '
                         '--resume to the rest, so a queue interrupted by a shared-GPU or '
                         'shared-model failure continues instead of restarting')
    ap.add_argument('--attempts-per-sample', type=int, default=1,
                    help='how many times to relaunch a sample whose run did not complete')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    index = []
    for directory in a.configs:
        index.extend(json.loads((directory / 'index.json').read_text()))
    log_dir = ROOT / 'outputs/logs'; log_dir.mkdir(parents=True, exist_ok=True)
    state_path = ROOT / 'outputs/diagnostics' / f'{a.tag}-queue.json'
    state = {'tag': a.tag, 'started_at': datetime.now(timezone.utc).isoformat(),
             'configs_dir': [str(p) for p in a.configs], 'workers': a.workers,
             'collection_config': str(a.collection_config), 'samples': []}
    pending = list(index)
    tracked = list(index)
    active = {}
    stop = {'flag': False}

    def halt(signum, frame):
        stop['flag'] = True
        for job in active.values():
            job['process'].terminate()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, halt)

    def record():
        state['samples'] = [{k: s.get(k) for k in
                             ('sample_id', 'house', 'case', 'mode', 'config', 'variant',
                              'run_id', 'gpu', 'status', 'started_at', 'ended_at', 'log',
                              'prior_status')}
                            for s in tracked]
        state['pending'] = len(pending); state['active'] = len(active)
        state['updated_at'] = datetime.now(timezone.utc).isoformat()
        state_path.write_text(json.dumps(state, indent=1) + '\n')

    for item in index:
        # `<sample_id>` alone collides between the augmented and the baseline
        # config directories (same sample, different scene), so the mode becomes
        # part of the run identity. The augmented suffix is left off so already
        # dispatched run directories keep their name and stay resumable.
        mode = item.get('mode', 'augmented')
        item['run_id'] = (f"{a.tag}-{item['sample_id']}" if mode == 'augmented'
                          else f"{a.tag}-{mode}-{item['sample_id']}")
    if a.resume:
        kept = []
        for item in index:
            run_dir = ROOT / 'outputs/no_edit' / item['run_id']
            summary = run_dir / 'scenes' / f"val_{item['house']}" / 'summary.json'
            status = json.loads(summary.read_text())['status'] if summary.is_file() else None
            if status == 'completed':
                print(f"[queue] skip {item['sample_id']}: already completed", flush=True)
            else:
                if run_dir.exists():
                    # A run directory already exists, so the collector must be told to
                    # reuse it -- even when it never got as far as writing a summary.
                    item['prior_status'] = status or 'no_summary'
                kept.append(item)
        pending = kept
    if a.dry_run:
        for item in pending:
            print(item['run_id'], item['config'])
        print(len(pending), 'configs')
        return

    attempts = {}
    while (pending or active) and not stop['flag']:
        for gpu, job in list(active.items()):
            if job['process'].poll() is not None:
                rc = job['process'].returncode
                item = job['item']
                summary = (ROOT / 'outputs/no_edit' / item['run_id'] / 'scenes'
                           / f"val_{item['house']}" / 'summary.json')
                status = json.loads(summary.read_text())['status'] if summary.is_file() else None
                item['status'] = status or f'exit_{rc}'
                item['ended_at'] = datetime.now(timezone.utc).isoformat()
                del active[gpu]
                if status != 'completed' and attempts.get(item['run_id'], 1) < a.attempts_per_sample:
                    attempts[item['run_id']] = attempts.get(item['run_id'], 1) + 1
                    item['prior_status'] = status or 'no_summary'
                    item['status'] = 'queued_retry'
                    item.pop('gpu', None)
                    pending.append(item)
                    print(f"[queue] {item['sample_id']} {status or rc} -> retry "
                          f"{attempts[item['run_id']]}/{a.attempts_per_sample}", flush=True)
                else:
                    print(f"[queue] {item['sample_id']} {item['status']} (gpu {gpu})", flush=True)
                record()
        while pending and len(active) < a.workers and not stop['flag']:
            free = [g for g in usable_gpus(a.gpu_ids, a.max_gpu_util, a.min_free_mb)
                    if g not in active]
            if not free:
                print('[queue] no free GPU passes the utilization/memory gate; waiting', flush=True)
                break
            gpu = free[0]
            item = pending.pop(0)
            log = log_dir / f"{item['run_id']}.log"
            env = dict(os.environ)
            if a.python:
                env['DATAFLOW_PYTHON'] = a.python
            env.setdefault('MUJOCO_GL', 'egl')
            env['PYTHONUNBUFFERED'] = '1'
            cmd = [str(ROOT / 'bin/lastmile-dataflow'), 'collect-no-edit',
                   '--assets-dir', str(a.assets_dir), '--config', item[a.config_key],
                   '--collection-config', str(a.collection_config),
                   '--run-id', item['run_id'], '--houses', str(item['house']),
                   '--gpu-ids', str(gpu), '--workers', '1']
            if item.get('prior_status') is not None:
                # Keep the finished attempts and only fill in what is missing.
                cmd.append('--resume')
            item['gpu'] = gpu; item['log'] = str(log)
            item['started_at'] = datetime.now(timezone.utc).isoformat()
            item['status'] = 'running'
            if item not in state['samples']:
                state['samples'].append(item)
            handle = log.open('ab')
            process = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT,
                                       cwd=str(ROOT), env=env)
            active[gpu] = {'process': process, 'item': item, 'sample_id': item['sample_id']}
            print(f"[queue] {item['run_id']} -> gpu {gpu}", flush=True)
            record()
        time.sleep(a.poll_seconds)
    for job in active.values():
        job['process'].terminate()
    record()
    print(f"[queue] done: {len(state['samples'])} dispatched, {len(pending)} undispatched", flush=True)


if __name__ == '__main__':
    main()
