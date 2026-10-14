#!/usr/bin/env python3
"""Build MolmoBot-native (`molmobot-native-v1`, 1024x576) inputs for scene_datasets samples.

The sealed scene_datasets packages bind the RBY-1 `position-v1` / 20 Hz / 320x240
configuration and are deliberately strategy-agnostic; the MolmoBot collector
requires native motors at 1024x576 and refuses a mismatched snapshot. This driver
produces the compatible input for every requested sample without touching the
sealed package:

1. `rebuild_augmented_native.py` replays the *already accepted* placements of the
   matching augmentation variant onto a native-profile scene (profile transplant,
   no new obstacle search, no new acceptance claim);
2. `revalidate_native_stations.py` re-checks **all** 161 dense candidate points
   under the native profile + 1024x576 visibility and re-selects the same
   coverage set;
3. the resulting directory is the `augmented_variant` input that `collect-no-edit`
   consumes for the VLA backend.

With `--baseline`, step 1 replays an *empty* placement list (the unedited twin), so
the same stations are scored on the scene without the added obstacles.
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

NATIVE_PY = '/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python'
VLA_BASE_CONFIG = ROOT / 'configs/scene_eval/native_vla_base.json'


def variants_for(dataset, mapping):
    """sample_id -> accepted augmentation variant directory (from the build diagnostics)."""
    return {row['sample_id']: Path(row['variant']).resolve() for row in mapping}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True, type=Path)
    ap.add_argument('--variant-map', required=True, type=Path,
                    help='JSON list of {sample_id, variant} produced by scripts/index_dataset_variants.py')
    ap.add_argument('--tag', required=True)
    ap.add_argument('--baseline', action='store_true',
                    help='transplant the unedited twin (empty placement list) instead')
    ap.add_argument('--gpu', type=int, default=2)
    ap.add_argument('--only', nargs='+', help='restrict to these sample ids')
    ap.add_argument('--python', default=NATIVE_PY)
    ap.add_argument('--skip-existing', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    dataset = a.dataset.resolve()
    mapping = variants_for(dataset, json.loads(a.variant_map.read_text()))
    rows = [json.loads(l) for l in (dataset / 'samples.jsonl').read_text().splitlines() if l.strip()]
    results = []
    for row in rows:
        sid = row['sample_id']
        if a.only and sid not in a.only:
            continue
        if row['case'] == 'Case3':
            results.append({'sample_id': sid, 'status': 'skipped_case3'})
            continue
        variant = mapping.get(sid)
        if variant is None:
            results.append({'sample_id': sid, 'status': 'no_variant_mapping'})
            continue
        prep_run = f'{a.tag}-prep-{sid}'
        val_run = f'{a.tag}-val-{sid}'
        prep_dir = ROOT / 'outputs/no_edit' / prep_run / 'scenes' / f'val_{row["house"]}' / 'scene'
        val_dir = ROOT / 'outputs/diagnostics' / val_run
        if a.skip_existing and (val_dir / 'summary.json').is_file():
            results.append({'sample_id': sid, 'status': 'exists', 'variant_dir': str(val_dir / 'variants' / 'v000')})
            continue
        if a.dry_run:
            print(sid, prep_run, val_run)
            continue
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), MUJOCO_GL='egl',
                   MUJOCO_EGL_DEVICE_ID=str(a.gpu), MPLCONFIGDIR=str(ROOT / 'outputs/dependencies/mpl'))
        prep_cmd = [a.python, str(ROOT / 'scripts/rebuild_augmented_native.py'),
                    '--variant', str(variant), '--run-id', prep_run]
        if a.baseline:
            prep_cmd.append('--no-placements')
        log = ROOT / 'outputs/logs' / f'{prep_run}.log'
        if (prep_dir / 'version.json').is_file():
            # Prep is idempotent per run-id and refuses to overwrite by design, so a
            # re-run after a downstream failure reuses the already-frozen snapshot.
            rc = 0
        else:
            with log.open('wb') as handle:
                rc = subprocess.run(prep_cmd, cwd=str(ROOT), env=env, stdout=handle,
                                    stderr=subprocess.STDOUT).returncode
        if rc != 0:
            results.append({'sample_id': sid, 'status': f'prep_exit_{rc}', 'log': str(log)})
            print(f'{sid}: prep failed (rc={rc})', flush=True)
            continue
        # val_104's unedited scene exceeds the segmentation-render path in this
        # MuJoCo build at wide FOV (native 139 deg vs the sealed package's 45 deg),
        # so its stations come from the unedited-twin evaluator instead.
        if row['house'] == 104:
            val_cmd = [a.python, str(ROOT / 'scripts/native_baseline_stations.py'),
                       '--native-scene', str(prep_dir), '--config', str(VLA_BASE_CONFIG),
                       '--task', str(dataset / row['path'] / 'task.json'), '--output', str(val_dir)]
        else:
            val_cmd = [a.python, str(ROOT / 'scripts/revalidate_native_stations.py'),
                       '--variant', str(variant), '--native-scene', str(prep_dir),
                       '--config', str(VLA_BASE_CONFIG), '--output', str(val_dir)]
        val_log = ROOT / 'outputs/logs' / f'{val_run}.log'
        with val_log.open('wb') as handle:
            rc = subprocess.run(val_cmd, cwd=str(ROOT), env=env, stdout=handle,
                                stderr=subprocess.STDOUT).returncode
        status = 'ok' if rc == 0 else f'val_exit_{rc}'
        results.append({'sample_id': sid, 'status': status, 'house': row['house'], 'case': row['case'],
                        'asset_id': row['asset_id'], 'operation': row['operation'],
                        'variant': str(variant), 'variant_dir': str(val_dir / 'variants' / 'v000'),
                        'prep_log': str(log), 'val_log': str(val_log),
                        'source_package': str(dataset / row['path'] / 'robot_sensor_configs')})
        print(f'{sid}: {status}', flush=True)
    out = ROOT / 'outputs/diagnostics' / f'{a.tag}-index.json'
    out.write_text(json.dumps(results, indent=1) + '\n')
    print(f'{len(results)} entries -> {out}')


if __name__ == '__main__':
    main()
