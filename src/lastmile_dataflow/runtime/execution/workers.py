"""CPU preparation, independent trial execution and recording-only outcomes."""
from contextlib import closing
from dataclasses import asdict
import os
from pathlib import Path
import time

from ..simulation import SnapshotCache
from .context import clone_sim
from .curobo import run_curobo_attempt
from .lifecycle import classify_episode_exception, execute_isolated_episode
from .preparation import prepare_station
from ...data.recorder import AttemptRecorder
from ...planning.remote import PlanningClient
from ...utils.io import write_json


_scenes = None


def initialize_preparation_worker():
    global _scenes
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    _scenes = SnapshotCache()


def prepare_job(scene, index, config, root):
    path = Path(root) / 'preparations' / scene['scene_id'] / scene['stations'][index]['station_id']
    try:
        baseline = _scenes.get(scene['scene_path'], scene['robot_config'], scene['task']['target_body'])
        prepared = prepare_station(baseline, scene['stations'][index], config, path)
        return {'status': 'ready', 'prepared': prepared}
    except Exception as exc:
        failure = classify_episode_exception(exc, 'initialize')
        write_json(path / 'preparation.json', failure)
        return failure


def record_unexecuted(scene, index, prepared, response, budget, config, collection, root):
    started = time.monotonic()
    identity = response.identity
    station = scene['stations'][index]

    def record():
        baseline = _scenes.get(scene['scene_path'], scene['robot_config'], scene['task']['target_body'])
        sim = clone_sim(baseline, target=scene['task']['target_body'])
        prepared.state.initialize(sim)
        recorder = AttemptRecorder(root, {'collection': asdict(collection), 'seed': identity.seed,
            'task': scene['task'], 'preparation': {'path': prepared.path, 'actual_base': prepared.actual_base}},
            attempt_id=identity.attempt_id)
        recorder.bind_simulation(sim, width=config.width, height=config.height)
        recorder.capture()
        write_json(recorder.path / 'initial_state.json', sim.observe_state())
        write_json(recorder.path / 'planning.json', [{'query_id': response.query_id,
            'stage': 'preclose_batch', **response.plan,
            'diagnostics': {**response.plan.get('diagnostics', {}), **response.diagnostics}}])
        reason = response.diagnostics.get('reason', response.status)
        recorder.finish(response.status, reason)
        budget.planning_s += response.planning_s
        budget.queue_s += response.queue_s + started - response.completed_at
        budget.plan_count = response.plan_count
        write_json(recorder.path / 'execution_diagnostics.json', {
            'active_budget': asdict(budget), 'trial_wall_s': time.monotonic() - budget.created_at,
            'recording_wall_s': time.monotonic() - started})
        return {**recorder.result, 'reason': reason, 'rollout_id': identity.attempt_id,
                'execution_termination': {'status': 'not_executed', 'reason': reason},
                'task_outcome': {'status': 'unknown', 'reason': reason}}

    return execute_isolated_episode(record, root=root, station=station, attempt_id=identity.attempt_id)


def execution_worker(index, jobs, planning_requests, planning_responses, results, stop,
                     config, collection, assets_dir, root, reuse_planners=True, *, runner=run_curobo_attempt):
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    with closing(SnapshotCache()) as scenes:
        while True:
            job = jobs.get()
            if job is None:
                return
            scene, station_index, prepared, response, budget, candidates = job
            identity = response.identity
            inputs = {'model_dir': scene['scene_path'], 'robot': scene['robot_config'],
                      'task': scene['task'], 'config': config, 'root': root, 'assets_dir': assets_dir,
                      'reuse_planners': reuse_planners}
            try:
                baseline = scenes.get(scene['scene_path'], scene['robot_config'], scene['task']['target_body'])
                baseline.scene_id = scene['scene_id']
                with closing(PlanningClient(index, planning_requests, planning_responses, identity,
                        budget, response, inputs, stop=stop)) as client:
                    result = runner(baseline, scene['task'], scene['stations'][station_index],
                        candidates, assets_dir, config, collection, root=root,
                        attempt_id=identity.attempt_id, seed=identity.seed, prepared=prepared,
                        planning_client=client)
            except Exception as exc:
                result = {**classify_episode_exception(exc, 'initialize'),
                          'rollout_id': identity.attempt_id, 'recording_complete': False}
            result['execution_worker'] = {'pid': os.getpid(), 'cuda_visible_devices': os.environ['CUDA_VISIBLE_DEVICES']}
            results.put((index, scene, station_index, identity, result))
