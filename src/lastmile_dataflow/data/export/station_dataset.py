"""Check physical stations and select up to 20 coverage stations on frozen scenes."""
import argparse
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from ...config import load_robot_config, robot_config
from ...scenes.dataset import load_scenes, select_stations
from ...scenes.dataset_config import SceneStationConfig
from ...utils.io import create_run, read_json, write_json, write_rows


def resample(dataset, robot_path, output):
    from ...runtime.simulation import Simulation
    from ...scenes.stations.sampling import sample_stations
    dataset = Path(dataset).resolve()
    selected_robot = load_robot_config(robot_path)
    values = read_json(dataset / 'config.json')['geometry'].copy()
    for key in ('radius_m', 'spacing_m'):
        values.pop(key, None)
    values.update(require_target_visibility=False, require_navigation_clearance=False)
    geometry = SceneStationConfig(**values)
    root = create_run(output, {'source_dataset': str(dataset), 'sampling_robot_config': str(Path(robot_path).resolve()),
        'geometry': asdict(geometry), 'station_selection': 'coverage', 'max_final_stations': 20})
    records, summaries = [], []
    for scene in load_scenes(dataset):
        path = Path(scene['scene_path'])
        source_robot = robot_config(read_json(path / 'config.json')['robot'])
        if (source_robot.name, source_robot.control_profile) != (selected_robot.name, selected_robot.control_profile):
            raise ValueError('resampling requires a matching robot and control profile')
        sim = Simulation.from_snapshot(path, source_robot, target=scene['task']['target_body'])
        try:
            candidates = sample_stations(sim, scene['task'], geometry)
            stations, selection = select_stations(candidates, scene['task']['anchor_world'], 'coverage', 20)
            relative = Path('scene-files') / scene['scene_id']
            sim.freeze(root / relative)
            write_json(root / relative / 'sampling.json', {'stations': candidates, 'selection': selection})
            records.append({key:value for key,value in scene.items() if key not in ('scene_path','stations','scene')})
            records[-1].update(scene=str(relative), stations=stations)
            summaries.append({'scene_id':scene['scene_id'], 'candidates':len(candidates), 'physical_valid':sum(s['geometry']=='valid' for s in candidates),
                'final_stations':len(stations),
                'low_visibility_kept':sum(s['visibility']['visible_pixels'] < geometry.min_target_pixels for s in stations),
                'exclusions':dict(Counter(s['reason'] for s in candidates if s['geometry']!='valid'))})
            print(summaries[-1], flush=True)
        finally:
            sim.close()
    write_rows(root / 'scenes.jsonl', records)
    write_json(root / 'summary.json', {'status':'completed', 'scenes':summaries})
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--robot-config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--gpu', type=int, required=True)
    args = parser.parse_args()
    import os
    os.environ.update(MUJOCO_GL='egl', CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu))
    print(resample(args.dataset, args.robot_config, args.output))


if __name__ == '__main__':
    main()
