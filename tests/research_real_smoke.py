"""Real Case sample initialization/stepping/RGB/export smoke, no planner or task-success claim."""
import argparse
from dataclasses import asdict
from pathlib import Path

from lastmile_dataflow.config import CollectionConfig, RobotConfig, construct
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.recording.protocol import rows
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.stations.scene_package import package_manifest, bind_package, package_stations
from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station
from lastmile_dataflow.validation.contacts import simulation_failure
from lastmile_dataflow.validation.canonical import export_inputs
from lastmile_dataflow.integrations.molmobot_dataset import export_molmobot


def smoke(package, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = package_manifest(package)
    bind_package(output, manifest)
    task = read_json(Path(package) / 'task.json')
    station_rows, _ = package_stations(output, task)
    station = station_rows[0]
    robot = construct(RobotConfig, read_json(output / 'scene/version.json')['robot'])
    sim = Simulation.from_snapshot(output / 'scene', robot, target=task['target_body'])
    recorder = None
    try:
        place_frozen_station(sim, station)
        config = {'robot': asdict(robot), 'collection': asdict(CollectionConfig(
            record_video=False, record_depth=True,
            width=manifest['binding']['station_geometry']['width'],
            height=manifest['binding']['station_geometry']['height'])),
            'task': task, 'station': station, 'seed': 1,
            'protocol': {'lastmile_selection': True},
            'scene_dataset_binding': {k: manifest[k] for k in
                ('scene_dataset_id', 'sample_id', 'robot_sensor_config_id', 'task_id', 'package_identity')}}
        recorder = AttemptRecorder(output, config, attempt_id='real-protocol-smoke',
                                   strategy='initialization_and_capture_not_task_demonstration')
        recorder.bind_simulation(sim)
        sim.begin()
        health = []
        for _ in range(2):
            before = sim.observe_state(); action = sim.robot.neutral_action()
            command, _ = sim.step(action)
            health.append(simulation_failure(sim))
            recorder.record_step(raw_action=action, command=command, before=before,
                after=sim.observe_state(), check={'valid': health[-1] is None}, observations={})
        recorder.finish('execution_complete', 'protocol_smoke_not_task_test',
            {'task_completion': {'status': 'unknown'}},
            extra={'trajectory_metrics': {'task_success': None, 'simulation_valid': not any(health)}})
        record, canonical, _ = export_inputs(recorder.path)
        export = export_molmobot(recorder.path, output / 'export', state_images_only=True, seed=1)
        result = {'case': manifest['case'], 'sample_id': manifest['sample_id'],
            'station_id': station['station_id'], 'executed_steps': canonical['executed_steps'],
            'frame_count': canonical['frame_count'], 'recording_complete': record['recording_complete'],
            'simulation_valid': record['simulation_valid'], 'task_success': record['task_success'],
            'export_samples': len(rows(export / 'samples.jsonl')),
            'CUDA_planner_tested': False, 'navigation_tested': False, 'task_success_tested': False}
        write_json(output / 'smoke.json', result)
        return result
    finally:
        if recorder is not None:
            recorder.abort_close()
        sim.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = smoke(args.package, args.output)
    print(result)
    raise SystemExit(0 if result['recording_complete'] and result['simulation_valid'] and result['export_samples'] else 1)
