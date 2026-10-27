"""Export station heatmaps for an existing calibration run, without new rollouts."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
import json
import os
from pathlib import Path
from types import SimpleNamespace

from lastmile_dataflow.config import read_config, robot_config
from lastmile_dataflow.data.evaluation.statistics import aggregate
from lastmile_dataflow.data.visualization.heatmap import export_heatmap
from lastmile_dataflow.planning.navigation.astar import scene_grid, task_support_obstacles
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.dataset import load_scenes
from lastmile_dataflow.utils.io import read_json, rows


def export_scene(job):
    scene, trials, config, output, skip_existing = job
    target = output / scene['scene_id']
    if skip_existing and target.exists():
        return {'scene_id': scene['scene_id'], 'status': 'skipped', 'path': str(target)}
    try:
        robot = robot_config(read_config(Path(scene['scene_path']) / 'config.json')['robot'])
        with closing(Simulation.from_snapshot(scene['scene_path'], robot,
                                              target=scene['task']['target_body'])) as sim:
            grid = scene_grid(sim, scene['task']['anchor_world'], config['radius_m'],
                              config['map_resolution_m'], 0., 0.,
                              support_obstacles=task_support_obstacles(sim, scene['task']))
            display = [{**row, 'attempt': row.get('rollout_id')} for row in trials]
            export_heatmap(target, grid, aggregate(scene['stations'], display, config['trials_per_station']),
                           scene['task']['anchor_world'],
                           config.get('smoothing_sigma_m') or config.get('spacing_m') or .1,
                           title=scene['scene_id'])
        return {'scene_id': scene['scene_id'], 'status': 'exported', 'path': str(target)}
    except Exception as exc:
        return {'scene_id': scene['scene_id'], 'status': 'error',
                'error': f'{type(exc).__name__}:{exc}'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线导出无编辑标定热图')
    parser.add_argument('--calibration', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--workers', type=int, default=16)
    parser.add_argument('--skip-existing', action='store_true')
    args = parser.parse_args(argv)
    run = args.calibration
    saved = read_json(run / 'config.json')
    config = saved['config']
    dataset = Path(saved['dataset'])
    scenes = load_scenes(dataset)
    by_scene = {}
    for row in rows(run / 'trials.jsonl'):
        by_scene.setdefault(row['scene_id'], []).append(row)
    output = args.output or (run / 'figures')
    output.mkdir(parents=True, exist_ok=True)
    jobs = [(scene, by_scene.get(scene['scene_id'], []), config, output, args.skip_existing) for scene in scenes]
    results = []
    with ProcessPoolExecutor(max_workers=max(1, min(args.workers, 64)),
                             mp_context=__import__('multiprocessing').get_context('spawn')) as pool:
        futures = [pool.submit(export_scene, job) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    manifest = run / 'heatmap_manifest.jsonl'
    manifest.write_text(''.join(json.dumps(row) + '\n' for row in results))
    errors = [row for row in results if row['status'] == 'error']
    print(json.dumps({'output': str(output), 'scenes': len(results), 'errors': len(errors),
                      'manifest': str(manifest)}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
