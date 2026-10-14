#!/usr/bin/env python3
"""Native-profile station package for the *unedited* twin of a scene_datasets sample.

`revalidate_native_stations.py` re-checks an accepted augmented layout: it replays
the variant's placements and re-runs the obstacle collision/support/Case checks.
For the unedited baseline there is no layout to accept, so this script does the
parts that still apply and nothing else:

* load the native-profile (`molmobot-native-v1`, 10 Hz, 1024x576) snapshot of the
  plain scene produced by `rebuild_augmented_native.py --no-placements`;
* evaluate **the same station set the augmented variant published** (passed in via
  `--stations`), each with a fresh Simulation, so the two runs differ only by the
  added obstacles and not by a re-selected set;
* re-check the same coverage selection over those results.

Output uses the exact on-disk variant layout `collect-no-edit` expects at
`<output>/variants/v000`, so the VLA baseline and the VLA augmented run consume
their inputs through the identical code path.
"""
import argparse, shutil, sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from lastmile_dataflow.config import RobotConfig, construct  # noqa: E402
from lastmile_dataflow.io import file_digest, read_json, write_json  # noqa: E402
from lastmile_dataflow.runtime.simulation import Simulation  # noqa: E402
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config  # noqa: E402
from lastmile_dataflow.stations.obstacle_selection import coverage_select  # noqa: E402
from lastmile_dataflow.workflows.obstacle_augmentation import evaluate_stations  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--native-scene', required=True, type=Path,
                    help='native-profile plain-scene snapshot')
    ap.add_argument('--stations', required=True, type=Path,
                    help="accepted augmented variant's stations_final.json (same coordinates)")
    ap.add_argument('--config', required=True, type=Path, help='MolmoBot NoEditConfig')
    ap.add_argument('--task', required=True, type=Path, help='task.json with anchor_world/target_body')
    ap.add_argument('--output', required=True, type=Path, help='new diagnostics run dir')
    ap.add_argument('--limit', type=int, default=20)
    a = ap.parse_args()

    config = load_no_edit_config(a.config)
    if config.operation_backend != 'molmobot' or config.augmented_variant:
        raise SystemExit('use a MolmoBot base config without augmented_variant')
    task = read_json(a.task)
    native = a.native_scene.resolve()
    if a.output.exists():
        raise SystemExit('refusing to overwrite: ' + str(a.output))

    version = read_json(native / 'version.json')
    robot = construct(RobotConfig, version['robot'])
    if robot.control_profile != 'molmobot-native-v1' or robot.control_hz != 10 or robot.physics_dt != .004:
        raise SystemExit('native MolmoBot robot profile required')

    stations = [{k: s[k] for k in ('station_id', 'xy', 'grid_index')} for s in read_json(a.stations)]
    if not stations:
        raise SystemExit('empty station set')
    a.output.mkdir(parents=True)
    target = a.output / 'variants' / 'v000'
    target.mkdir(parents=True)
    write_json(a.output / 'summary.json', {'status': 'running', 'scope': 'native_unedited_baseline_stations'})
    sim = Simulation.from_snapshot(native, robot, target=task['target_body'])
    state = sim.state_vector().copy()
    try:
        rows = evaluate_stations(sim, task, config, frozen_stations=stations)
        final, selection = coverage_select(rows, task['anchor_world'], a.limit)
        if not np.array_equal(state, sim.state_vector()):
            raise RuntimeError('station validation changed the source scene state')
        if not final:
            raise RuntimeError('no baseline station passed native initialization and visibility')
        write_json(target / 'navigation/comparison.json',
                   {'stations_after': rows, 'stations_before': rows,
                    'note': 'unedited twin; the augmented variant station set; '
                            'native profile and 1024x576 visibility'})
        write_json(target / 'stations_final.json', final)
        write_json(target / 'station_selection.json', selection)
        write_json(target / 'checks.json',
                   {'valid': True, 'scope': 'no_obstacle_placements_to_accept',
                    'task_completion': 'unknown'})
        write_json(target / 'edits.json', [])
        shutil.copytree(native, target / 'scene')
        write_json(target / 'native_revalidation.json',
                   {'schema_version': 'native-station-revalidation-v1',
                    'source_variant': None, 'native_scene': str(native),
                    'augmented_stations': str(a.stations),
                    'dense_candidate_count': len(stations),
                    'actual_valid_count': selection['input_count'], 'final_count': len(final),
                    'source_scene_state_unchanged': True, 'no_new_physics_steps': True,
                    'robot': asdict(robot), 'validation_config': asdict(config),
                    'candidate_sha256': None, 'parent_checks_sha256': None,
                    'edits_sha256': None,
                    'native_scene_files_sha256': {p.name: file_digest(p)
                                                  for p in sorted(native.iterdir()) if p.is_file()}})
        write_json(a.output / 'summary.json',
                   {'status': 'completed', 'accepted_count': 1, 'case': None,
                    'dense_candidate_count': len(stations),
                    'actual_valid_count': selection['input_count'],
                    'final_station_count': len(final),
                    'execution_scope': 'native_profile_unedited_station_set_revalidation',
                    'task_completion': 'unknown',
                    'variants': [{'variant_id': 'v000', 'status': 'accepted'}]})
        print(target)
    except BaseException as exc:
        write_json(a.output / 'summary.json', {'status': 'failed', 'reason': str(exc)})
        raise
    finally:
        sim.close()


if __name__ == '__main__':
    main()
