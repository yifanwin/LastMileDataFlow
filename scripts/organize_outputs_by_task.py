"""Expose figures, preparations, planning, rollouts and replays by task/object."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from lastmile_dataflow.data.evaluation.failure_analysis import _scene_summary

GROUPS = ('pick', 'open')


def safe(name):
    value = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(name or 'unknown')).strip('_')
    return value or 'unknown'


def load_run(path):
    config = json.loads((path / 'config.json').read_text())
    dataset = Path(config['dataset'])
    scenes = []
    for line in (dataset / 'scenes.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        try:
            scene = json.loads(line)
        except ValueError:
            continue
        if scene.get('status') == 'accepted':
            scenes.append(scene)
    trials = []
    for line in (path / 'trials.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        try:
            trial = json.loads(line)
        except ValueError:
            continue
        trials.append(trial)
    by_scene = {}
    trial_scene = {}
    trial_status = {}
    for trial in trials:
        if trial.get('rollout_id'):
            trial_status[trial['rollout_id']] = trial.get('status')
            if trial.get('scene_id'):
                trial_scene[trial['rollout_id']] = trial['scene_id']
        by_scene.setdefault(trial.get('scene_id'), []).append(trial)
    scene_map = {}
    valid_map = {}
    for scene in scenes:
        task = scene.get('task') or {}
        if task.get('operation') and task.get('category'):
            scene_map[scene['scene_id']] = (safe(task['operation']), safe(task['category']))
        summary = _scene_summary(scene, by_scene.get(scene['scene_id'], []))
        valid_map[scene['scene_id']] = summary['successes'] > 0
    return scene_map, trial_scene, valid_map, trial_status

def link(base, operation, category, valid, name, report):
    category_dir = base / operation / category
    valid_dir = 'valid_task' if valid else 'no_valid_task'
    other_dir = 'no_valid_task' if valid else 'valid_task'
    legacy = category_dir / name
    if legacy.is_symlink() and os.readlink(legacy) == f'../../{name}':
        legacy.unlink()
    for old_valid in ('valid_scene', 'no_valid_scene'):
        old = category_dir / old_valid / name
        if old.is_symlink() and os.readlink(old) == f'../../../{name}':
            old.unlink()
        try:
            (category_dir / old_valid).rmdir()
        except OSError:
            pass
    wrong = category_dir / other_dir / name
    if wrong.is_symlink() and os.readlink(wrong) == f'../../../{name}':
        wrong.unlink()
    destination = category_dir / valid_dir / name
    if destination.is_symlink():
        if os.readlink(destination) == f'../../../{name}':
            report['existing'] += 1
            return
        report['conflicts'] += 1
        return
    if destination.exists():
        report['conflicts'] += 1
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(f'../../../{name}', target_is_directory=True)
    report['created'] += 1

def organize_run(path, report):
    scene_map, trial_scene, valid_map, _ = load_run(path)
    for artifact in ('figures', 'preparations'):
        base = path / artifact
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir() or entry.name in GROUPS:
                continue
            target = scene_map.get(entry.name)
            if not target:
                report['unmapped'] += 1
                continue
            link(base, target[0], target[1], valid_map.get(entry.name, False), entry.name, report)
    base = path / 'rollouts'
    if base.is_dir():
        for entry in base.iterdir():
            if not entry.is_dir() or entry.name in GROUPS:
                continue
            scene = trial_scene.get(entry.name)
            target = scene_map.get(scene) if scene else None
            if not target:
                report['unmapped'] += 1
                continue
            link(base, target[0], target[1], valid_map.get(scene, False), entry.name, report)
    base = path / 'planning'
    if base.is_dir():
        for entry in base.iterdir():
            if not entry.is_dir() or entry.name in GROUPS:
                continue
            match = re.match(r'(r\d+)', entry.name)
            scene = trial_scene.get(match.group(1)) if match else None
            target = scene_map.get(scene) if scene else None
            if not target:
                report['unmapped'] += 1
                continue
            link(base, target[0], target[1], valid_map.get(scene, False), entry.name, report)


def clear_old_view(replay):
    for group in GROUPS:
        path = replay / group
        if not path.is_dir() or path.is_symlink():
            continue
        has_file = False
        for root, _, files in os.walk(path, followlinks=False):
            if any(not (Path(root) / name).is_symlink() for name in files):
                has_file = True
                break
        if not has_file:
            shutil.rmtree(path)


def link_scene_video(base, operation, category, valid, outcome, scene, source, report):
    destination_dir = base / operation / category / (
        'valid_task' if valid else 'no_valid_task') / outcome
    scene = safe(scene)
    for suffix in ('', f'__{source.name}', f'__{source.parent.name}__{source.name}'):
        name = scene + suffix
        for old_valid in ('valid_scene', 'no_valid_scene'):
            for old_outcome in ('success', 'failure'):
                old = base / operation / category / old_valid / old_outcome / name
                if old.is_symlink() and os.readlink(old) == os.path.relpath(source, old.parent):
                    old.unlink()
                try:
                    old.parent.rmdir()
                except OSError:
                    pass
            try:
                (base / operation / category / old_valid).rmdir()
            except OSError:
                pass
        destination = destination_dir / name
        target = os.path.relpath(source, destination_dir)
        if destination.is_symlink():
            if os.readlink(destination) == target:
                report['existing'] += 1
                return
            continue
        if destination.exists():
            report['conflicts'] += 1
            return
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target, target_is_directory=True)
        report['created'] += 1
        return
    report['conflicts'] += 1

def organize_replay(replay, run_cache, report, base):
    clear_old_view(replay)
    for entry in replay.iterdir():
        if not entry.is_dir() or entry.name in GROUPS:
            continue
        video = entry / 'video.json'
        if not video.is_file():
            report['unmapped'] += 1
            continue
        try:
            source = Path(json.loads(video.read_text()).get('rollout', ''))
        except (OSError, ValueError):
            report['unmapped'] += 1
            continue
        calibration = source.parent.parent
        rollout = source.name
        if not calibration.is_dir():
            report['unmapped'] += 1
            continue
        key = str(calibration)
        if key not in run_cache:
            run_cache[key] = load_run(calibration)
        scene_map, trial_scene, valid_map, trial_status = run_cache[key]
        scene = trial_scene.get(rollout)
        target = scene_map.get(scene) if scene else None
        if not target:
            report['unmapped'] += 1
            continue
        outcome = 'success' if trial_status.get(rollout) == 'success' else 'failure'
        link_scene_video(base, target[0], target[1], valid_map.get(scene, False), outcome,
                         scene, entry, report)


def new_report():
    return {'created': 0, 'existing': 0, 'conflicts': 0, 'unmapped': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description='按 pick/open 和目标类别整理输出视图')
    parser.add_argument('--output-dir', type=Path, default=Path('outputs'))
    parser.add_argument('--runs', nargs='+', type=Path)
    parser.add_argument('--replays', nargs='+', type=Path)
    parser.add_argument('--replay-view', type=Path,
                        help='回放分类视图根目录（默认 <output-dir>/replays）；新一轮重跑同一批任务时单独放，避免与旧视图重名')
    args = parser.parse_args(argv)
    runs = args.runs or sorted(path for path in args.output_dir.iterdir()
                               if path.is_dir() and (path / 'trials.jsonl').is_file())
    source = args.output_dir / 'replays'
    replays = args.replays or sorted(path for path in source.iterdir()
                                     if path.is_dir() and path.name not in GROUPS)
    base = args.replay_view or source
    run_cache = {}
    summary = {}
    for run in runs:
        report = new_report()
        organize_run(run, report)
        summary[run.name] = report
    for replay in replays:
        report = new_report()
        organize_replay(replay, run_cache, report, base)
        if any(report.values()):
            summary[replay.name] = report
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
