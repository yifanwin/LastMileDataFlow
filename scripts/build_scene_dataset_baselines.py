#!/usr/bin/env python3
"""Derive "unedited scene" baseline packages from a sealed scene_datasets root.

Each sample carries a physically-identical unedited twin at
`<sample>/construction/control_scene` (same source XML/metadata, same robot/sensor
binding, same candidate lattice and the same 20 selected stations). This tool
re-publishes that twin as a `scene-station-package-v1` package so `collect-no-edit`
can consume it through the existing `scene_package` path with **no code change**:
same stations, same station geometry, only the obstacle placements removed.

It is read-only with respect to the source dataset: nothing under the source root
is written, and every derived package carries a `baseline_of` record pointing back
at the augmented package it mirrors.
"""
import argparse, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from lastmile_dataflow.io import file_digest, read_json, write_json  # noqa: E402
from lastmile_dataflow.stations.scene_package import package_manifest  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path,
                    help='new root holding <sample_id>/robot_sensor_configs/<config_id>')
    a = ap.parse_args()
    root = a.dataset.resolve(); out = a.out.resolve()
    if out.exists():
        raise SystemExit('refusing to overwrite existing baseline root: ' + str(out))
    rows = [json.loads(l) for l in (root / 'samples.jsonl').read_text().splitlines() if l.strip()]
    written = []
    for row in rows:
        if row['case'] == 'Case3':
            continue  # Case3 adds no objects; its control twin would be the same scene
        sample = root / row['path']
        control = sample / 'construction/control_scene'
        if not control.is_dir():
            raise SystemExit(f'{row["sample_id"]}: missing control_scene')
        cids = row['robot_sensor_config_ids']
        if len(cids) != 1:
            raise SystemExit(f'{row["sample_id"]}: expected 1 config id')
        source = sample / 'robot_sensor_configs' / cids[0]
        dest = out / row['sample_id'] / 'robot_sensor_configs' / cids[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest)
        shutil.rmtree(dest / 'scene')
        shutil.copytree(control, dest / 'scene')
        manifest = read_json(dest / 'manifest.json')
        manifest['baseline_of'] = {
            'schema_version': 'scene-dataset-baseline-v1',
            'source_dataset': str(root), 'source_package': str(source),
            'source_sample_id': row['sample_id'], 'case': row['case'],
            'scene': 'construction/control_scene',
            'placements_removed': read_json(sample / 'construction/edits.json'),
            'stations': 'identical to the augmented package (same selected.json, not regenerated)',
        }
        write_json(dest / 'manifest.json', manifest)
        # checksums.json never covers itself (mirrors the sealed-package writer).
        (dest / 'checksums.json').unlink(missing_ok=True)
        write_json(dest / 'checksums.json',
                   {str(q.relative_to(dest)): file_digest(q) for q in dest.rglob('*') if q.is_file()})
        package_manifest(dest)  # re-verify the derived package with the same reader
        written.append({'sample_id': row['sample_id'], 'house': row['house'], 'scene_id': row['scene_id'],
                        'case': row['case'], 'asset_id': row['asset_id'], 'operation': row['operation'],
                        'station_count': row['station_count'],
                        'package': str(dest)})
        print('baseline package', row['sample_id'], flush=True)
    write_json(out / 'index.json', written)
    print(f'{len(written)} baseline packages -> {out}')


if __name__ == '__main__':
    main()
