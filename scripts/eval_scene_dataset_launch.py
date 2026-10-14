#!/usr/bin/env python3
"""Generate one NoEditConfig JSON per scene_datasets sample for strategy evaluation.

`collect-no-edit` binds exactly one `scene_package` and one house per run, so each
sample needs its own config file. Two modes:

* `augmented` (default) reads the sealed package shipped in the dataset root;
* `baseline` reads a derived unedited twin built by
  `scripts/build_scene_dataset_baselines.py` (`--baseline-root <dir>`).

Both modes are consumed through the same public `scene_package` path, so no
collector code differs between them; only the package (and therefore the frozen
scene) differs.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

BASE_CUROBO = {
    "schema_version": "no-edit-v1", "operation_base_mode": "holonomic_joint_planning",
    "torso_error_policy": "record_only", "third_person_enabled": True,
    "radius_m": 0.7928056080178096, "spacing_m": 0.1108796040001314,
    "trials_per_station": 5, "discard_below": 0.05, "start_count": 3,
    "min_successful_rollouts": 1, "operation_retries": 1, "operations": ["pick", "open"],
    "min_target_pixels": 24, "map_resolution_m": 0.05, "smoothing_sigma_m": None,
    "smoothing_support_distance_m": 0.35, "attempt_timeout_s": 300.0,
    "scene_timeout_s": 86400.0, "width": 320, "height": 240, "min_free_disk_gb": 5.0,
    "seed": 20261009, "planner_backend": "curobo_v2_v080", "max_attempts": 5,
}
BASE_VLA = {
    "schema_version": "no-edit-v1", "operation_backend": "molmobot",
    "policy_config": str((ROOT / 'configs/policies/molmobot.json').resolve()),
    "operation_base_mode": "policy_relative", "torso_error_policy": "record_only",
    "third_person_enabled": True, "radius_m": 0.7928056080178096,
    "spacing_m": 0.1108796040001314, "trials_per_station": 5, "operations": ["pick", "open"],
    "min_target_pixels": 24, "map_resolution_m": 0.05, "smoothing_sigma_m": None,
    "smoothing_support_distance_m": 0.35, "attempt_timeout_s": 300.0,
    "scene_timeout_s": 86400.0, "width": 1024, "height": 576, "min_free_disk_gb": 5.0,
    "seed": 20261011,
}


def samples(root):
    rows = [json.loads(line) for line in (root / 'samples.jsonl').read_text().splitlines() if line.strip()]
    if not rows:
        raise SystemExit('empty samples.jsonl')
    return rows


def resolve_package(root, row, baseline_root):
    if baseline_root is None:
        base = root / row['path'] / 'robot_sensor_configs'
    else:
        base = baseline_root / row['sample_id'] / 'robot_sensor_configs'
    manifests = sorted(base.glob('*/manifest.json'))
    if len(manifests) != 1:
        raise SystemExit(f'{row["sample_id"]}: expected 1 package under {base}, got {len(manifests)}')
    return manifests[0].parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--backend', default='curobo', choices=['curobo', 'vla'])
    ap.add_argument('--baseline-root', type=Path, default=None,
                    help='derived unedited-package root; omit for augmented packages')
    ap.add_argument('--trials-per-station', type=int, default=5)
    a = ap.parse_args()
    root = a.dataset.resolve()
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    mode = 'baseline' if a.baseline_root else 'augmented'
    base = dict(BASE_CUROBO if a.backend == 'curobo' else BASE_VLA)
    base['trials_per_station'] = a.trials_per_station
    index = []; skipped = []
    for row in samples(root):
        if mode == 'baseline' and row['case'] == 'Case3':
            # Case3 adds no objects, so its unedited twin is the same scene.
            skipped.append({'sample_id': row['sample_id'], 'reason': 'case3_adds_no_objects'})
            continue
        package = resolve_package(root, row, a.baseline_root.resolve() if a.baseline_root else None)
        cfg = dict(base); cfg['scene_package'] = str(package)
        path = out / f'{row["sample_id"]}.{mode}.{a.backend}.json'
        path.write_text(json.dumps(cfg, indent=2) + '\n')
        index.append({'sample_id': row['sample_id'], 'house': row['house'], 'scene_id': row['scene_id'],
                      'case': row['case'], 'asset_id': row['asset_id'], 'operation': row['operation'],
                      'station_count': row['station_count'], 'instance_id': row['instance_id'],
                      'task_id': row['task_id'], 'mode': mode, 'config': str(path), 'package': str(package)})
    (out / 'index.json').write_text(json.dumps(index, indent=1) + '\n')
    if skipped:
        (out / 'skipped.json').write_text(json.dumps(skipped, indent=1) + '\n')
    print(f'{len(index)} {mode} {a.backend} configs -> {out}')


if __name__ == '__main__':
    main()
