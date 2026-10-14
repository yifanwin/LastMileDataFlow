#!/usr/bin/env python3
"""One MolmoBot NoEditConfig per scene_datasets sample, bound to a native-profile variant.

The sealed package binds `position-v1` / 320x240 and the VLA collector refuses a
mismatched snapshot, so VLA evaluation consumes an `augmented_variant` directory
produced by `scripts/prepare_native_dataset_variants.py` (augmented) or
`scripts/native_baseline_stations.py` (unedited twin) instead. Both are the same
on-disk layout, so the run path is identical between the two modes.
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = {
    "schema_version": "no-edit-v1", "operation_backend": "molmobot",
    "policy_config": "../policies/molmobot.json", "operation_base_mode": "policy_relative",
    "torso_error_policy": "record_only", "third_person_enabled": True,
    "radius_m": 0.7928056080178096, "spacing_m": 0.1108796040001314,
    "trials_per_station": 5, "operations": ["pick", "open"], "min_target_pixels": 24,
    "map_resolution_m": 0.05, "smoothing_sigma_m": None, "smoothing_support_distance_m": 0.35,
    "attempt_timeout_s": 300.0, "scene_timeout_s": 86400.0, "width": 1024, "height": 576,
    "min_free_disk_gb": 5.0, "seed": 20261011,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True, type=Path)
    ap.add_argument('--variant-root', required=True, type=Path,
                    help='dir holding <sample_id>/variants/<vid>/ (native variant root)')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--sample-dir-prefix', default='',
                    help='prefix joined to <sample_id> to locate the variant root, '
                         'e.g. nativeAug-val-')
    ap.add_argument('--mode', required=True, choices=['augmented', 'baseline'],
                    help='run-identity mode; keeps augmented and baseline run ids distinct')
    ap.add_argument('--only', nargs='+')
    ap.add_argument('--trials-per-station', type=int, default=5)
    a = ap.parse_args()
    root = a.dataset.resolve(); vroot = a.variant_root.resolve()
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    index = []; skipped = []
    for line in (root / 'samples.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sid = row['sample_id']
        if a.only and sid not in a.only:
            continue
        sample_dir = vroot / (a.sample_dir_prefix + sid)
        variants = sorted((sample_dir / 'variants').glob('*')) if (sample_dir / 'variants').is_dir() else []
        if len(variants) != 1:
            skipped.append({'sample_id': sid, 'reason': 'no_single_native_variant', 'found': len(variants)})
            continue
        cfg = dict(BASE); cfg['policy_config'] = str((ROOT / 'configs/policies/molmobot.json').resolve())
        cfg['trials_per_station'] = a.trials_per_station
        cfg['augmented_variant'] = str(variants[0].resolve())
        path = out / f'{sid}.vla.json'
        path.write_text(json.dumps(cfg, indent=2) + '\n')
        index.append({'sample_id': sid, 'house': row['house'], 'scene_id': row['scene_id'],
                      'case': row['case'], 'asset_id': row['asset_id'], 'operation': row['operation'],
                      'mode': a.mode, 'config': str(path), 'variant': str(variants[0].resolve())})
    (out / 'index.json').write_text(json.dumps(index, indent=1) + '\n')
    if skipped:
        (out / 'skipped.json').write_text(json.dumps(skipped, indent=1) + '\n')
    print(f'{len(index)} vla configs -> {out}  ({len(skipped)} skipped)')
    for s in skipped:
        print('  skipped', s['sample_id'], s['reason'])


if __name__ == '__main__':
    main()
