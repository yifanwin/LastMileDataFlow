#!/usr/bin/env python3
"""Frozen calibration input holding selected stations of one existing scene.

Copies the scene's baseline files, keeps only the listed stations in their
given order, and writes the repository execution config with `seed`, so the
trial seeds (seed, seed+1, ...) can equal those of the source run.
"""
import argparse
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True, help='existing input directory')
    parser.add_argument('--scene', required=True, help='scene_id')
    parser.add_argument('--stations', nargs='+', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--execution-config', type=Path, default=Path('configs/execution/panda_omron_no_edit_curobo.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scene = next(s for s in map(json.loads, (args.source / 'scenes.jsonl').read_text().splitlines())
                 if s['scene_id'] == args.scene)
    stations = {s['station_id']: s for s in scene['stations']}
    scene['stations'] = [stations[s] for s in args.stations]
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.source / scene['scene'], args.output / scene['scene'])
    for name in ('config.json', 'summary.json'):
        shutil.copy2(args.source / name, args.output / name)
    (args.output / 'scenes.jsonl').write_text(json.dumps(scene) + '\n')
    execution = {**json.loads(args.execution_config.read_text()), 'seed': args.seed}
    (args.output / f'execution_seed{args.seed}.json').write_text(json.dumps(execution, indent=2) + '\n')


if __name__ == '__main__':
    main()
