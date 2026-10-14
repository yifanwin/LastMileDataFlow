"""Explicit V2 rollout on a scene-dataset; excluded from unittest discovery."""
import argparse
from dataclasses import asdict
from pathlib import Path

from lastmile_dataflow.config import CollectionConfig, RobotConfig
from lastmile_dataflow.io import create_run, read_json, write_json
from lastmile_dataflow.runtime.no_edit_execution import run_raw_attempt
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.stations.scene_package import load_scenes
from lastmile_dataflow.tasks.raw_scene import grasp_candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--scene-id', required=True)
    parser.add_argument('--station', required=True)
    parser.add_argument('--assets-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path('configs/no_edit/curobo_v2.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    config = load_no_edit_config(args.config)
    scene = next(row for row in load_scenes(args.dataset, [args.scene_id]))
    station = next(row for row in scene['stations'] if row['station_id'] == args.station)
    robot = RobotConfig(**read_json(Path(scene['scene_path']) / 'config.json')['robot'])
    collection = CollectionConfig(record_video=False, width=config.width, height=config.height, seed=config.seed)
    root = create_run(args.output, {'config': asdict(config), 'seed': config.seed, 'scene': scene['scene_id']})
    sim = Simulation.from_snapshot(scene['scene_path'], robot, target=scene['task']['target_body'])
    sim.scene_id = scene['scene_id']
    try:
        result = run_raw_attempt(sim, scene['task'], station, grasp_candidates(scene['task'], args.assets_dir),
            args.assets_dir, config, collection, root, 'smoke', seed=config.seed)
        write_json(root / 'result.json', result)
        print(result['status'])
    finally:
        sim.close()


if __name__ == '__main__':
    main()
