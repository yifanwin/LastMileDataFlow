"""New media-only run from immutable completed physical evidence; no re-sampling.

Only recording/obstacle_views.py may differ from the physical parent's source
manifest. Snapshots and all dense-station results are copied byte-for-byte.
"""
import argparse
from dataclasses import asdict
from pathlib import Path
import shutil
import time

import numpy as np

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.construction.obstacle_config import ObstacleConfig
from lastmile_dataflow.exporting.obstacle_augmentation import export_browser
from lastmile_dataflow.io import digest, file_digest, read_json, write_json
from lastmile_dataflow.recording.edit_views import ViewConfig
from lastmile_dataflow.recording.obstacle_views import render_obstacle_pair
from lastmile_dataflow.runtime.preparation import SettleConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.graph import build_scene_graph


def rerender(parent, output):
    parent, output = Path(parent).resolve(), Path(output).resolve()
    start = time.monotonic()
    summary = read_json(parent / 'summary.json')
    if summary['status'] != 'completed' or summary['accepted_count'] != 5:
        raise ValueError('completed physical parent with five variants required')
    provenance = read_json(parent / 'provenance.json')
    package = Path(__file__).resolve().parents[1] / 'src/lastmile_dataflow'
    code = {str(p.relative_to(package)): file_digest(p) for p in package.rglob('*.py')}
    changed = {key for key in code.keys() | provenance['source_files'].keys()
               if code.get(key) != provenance['source_files'].get(key)}
    if changed != {'recording/obstacle_views.py'}:
        raise ValueError('physical algorithm changed; full construction rerun required: ' + str(changed))
    excluded = {'rgb', 'camera_trials', 'camera_search_failed.json', 'renderer.json', 'audit.json',
                'index.html', 'media_manifest.json', 'before_after_overview.jpg', 'provenance.json',
                'summary.json', 'artifact_retention.json'}
    shutil.copytree(parent, output, ignore=lambda path, names: [n for n in names if n in excluded])
    frozen = read_json(output / 'frozen_config.json')
    config = ObstacleConfig(**frozen['augmentation'])
    robot = RobotConfig(**frozen['robot'])
    settle = SettleConfig(**config.settle)
    task = read_json(output / 'baseline/task.json')
    provenance.update(physical_parent_run=str(parent), physical_parent_code_sha256=provenance['code_sha256'],
                      source_files=code, code_sha256=digest(code), changed_modules=sorted(changed),
                      execution_scope='media_only; physical_construction_and_all_station_checks_reused_byte_identically_from_parent')
    write_json(output / 'provenance.json', provenance)
    summary.update(run_id=output.name, status='incomplete', physical_parent_run=str(parent),
                   physical_elapsed_s=summary['elapsed_s'], execution_scope=provenance['execution_scope'])
    write_json(output / 'summary.json', summary)
    for variant in summary['variants']:
        ident = variant['variant_id']
        folder = output / 'variants' / ident
        before = Simulation.from_snapshot(folder / 'before_scene', robot, target=config.target_instance)
        after = Simulation.from_snapshot(folder / 'scene', robot, target=config.target_instance)
        try:
            state0, state1 = before.state_vector().copy(), after.state_vector().copy()
            before_graph = build_scene_graph(before, stage='observed', config=settle.graph_config())
            after_graph = build_scene_graph(after, stage='settled', revision=1, config=settle.graph_config())
            names = {f'procedural_obstacle_{i}' for i in range(variant['requested_count'])}
            context = {task['target_body']} | {s['body'] for s in task['support_obstacles']}
            packet = render_obstacle_pair(before, after, before_graph, after_graph, names,
                       task['target_body'], context, folder / 'rgb', sample_id=ident,
                       config=ViewConfig(**config.views), min_pixels=frozen['baseline']['min_target_pixels'],
                       coverage_union=config.case_policy.case == 'Case2')
            if not (np.array_equal(state0, before.state_vector()) and np.array_equal(state1, after.state_vector())):
                raise ValueError('rendering_changed_physical_state')
            write_json(folder / 'renderer.json', packet['renderer_info'])
            print(ident, 'rendered', packet['selected_camera_trial'], flush=True)
        finally:
            before.close(); after.close()
    # Byte equality proves these are the SAME previously initialized scenes/stations.
    unchanged = {}
    for file in output.rglob('*'):
        if file.is_file() and (file.name in ('model.mjb', 'initial.npz') or file.name == 'comparison.json'):
            relative = file.relative_to(output)
            unchanged[str(relative)] = file_digest(file) == file_digest(parent / relative)
    if not all(unchanged.values()):
        raise ValueError('physical_parent_evidence_changed')
    write_json(output / 'media_repair.json', {'physical_parent': str(parent), 'changed_modules': sorted(changed),
               'physics_steps': 0, 'robot_initializations_in_this_media_run': 0,
               'all_parent_snapshots_and_dense_station_results_identical': unchanged,
               'physical_state_unchanged_by_rendering': True})
    summary.update(status='completed', elapsed_s=time.monotonic() - start, camera_repair_elapsed_s=time.monotonic() - start)
    write_json(output / 'summary.json', summary)
    export_browser(output, summary)
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('parent', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rerender(args.parent, args.output)
