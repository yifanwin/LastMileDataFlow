"""Bounded station preparation, resident GPU planning and independent CPU trials."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import time

from ..planning.pool import PlanningPool
from ..runtime.execution.protocol import PlanningRequest, PlanningResponse, TrialBudget, TrialIdentity
from ..runtime.execution.workers import execution_worker, initialize_preparation_worker, prepare_job, record_unexecuted
from ..scenes.tasks.discovery import grasp_candidates
from ..utils.io import append_row


def calibrate_stations(scenes, assets_dir, config, collection, root, resources, *,
                       attempt_ids, max_trials=None, service_type=None, trial_worker=execution_worker):
    """One run-wide scheduler; only this parent appends the raw trial file."""
    from .collection import _shared_failure, _trial_record

    started = time.monotonic()
    context = multiprocessing.get_context('spawn')
    planned = sum(min(len(s['stations']) * config.trials_per_station,
                      max_trials if max_trials is not None else len(s['stations']) * config.trials_per_station)
                  for s in scenes)
    cpu_count = min(resources.cpu_workers, planned)
    original_cuda = os.environ.get('CUDA_VISIBLE_DEVICES')
    os.environ.update(CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    stop = context.Event()
    requests, results = context.Queue(), context.Queue()
    jobs = [context.Queue() for _ in range(cpu_count)]
    replies = [context.Queue() for _ in range(cpu_count)]
    cpus = [context.Process(target=trial_worker, args=(index, jobs[index], requests, replies[index],
            results, stop, config, collection, assets_dir, root, resources.reuse_planners)) for index in range(cpu_count)]
    prep = ProcessPoolExecutor(max_workers=resources.preparation_workers, mp_context=context,
                               initializer=initialize_preparation_worker)
    pool = PlanningPool(resources.gpu_ids, **({'service_type': service_type} if service_type else {}))
    for process in cpus:
        process.start()
    preparations, batches, recordings, routes = {}, {}, {}, {}
    ready, active, idle = deque(), {}, deque(range(cpu_count))
    trials, preparation_failures = [], []
    failure = None
    peaks = {'cpu_trials': 0, 'gpu_queries': 0, 'preparations': 0}
    gpu_active_s = 0.

    def stations():
        for scene in scenes:
            remaining = min(len(scene['stations']) * config.trials_per_station,
                            max_trials if max_trials is not None else len(scene['stations']) * config.trials_per_station)
            for index in range(len(scene['stations'])):
                size = min(config.trials_per_station, remaining)
                if size <= 0:
                    break
                identities = [TrialIdentity(scene['scene_id'], index, trial,
                    f'r{next(attempt_ids):06d}', config.seed + index * config.trials_per_station + trial)
                    for trial in range(size)]
                yield scene, index, identities, [TrialBudget(config.attempt_timeout_s) for _ in identities]
                remaining -= size

    units = iter(stations())
    exhausted = False
    capacity = resources.cpu_workers + config.trials_per_station * len(resources.gpu_ids)

    def save(scene, index, identity, result):
        nonlocal failure
        record = _trial_record(result, scene, scene['stations'][index], identity.trial, identity.seed)
        record['station_index'] = index
        append_row(Path(root) / 'trials.jsonl', record)
        trials.append(record)
        if _shared_failure(record):
            failure = result
            stop.set()

    def receive_cpu(message):
        worker, scene, index, identity, result = message
        active.pop(worker)
        idle.append(worker)
        save(scene, index, identity, result)

    def record_later(scene, index, prepared, response, budget):
        future = prep.submit(record_unexecuted, scene, index, prepared, response,
                             budget, config, collection, root)
        recordings[future] = (scene, index, response.identity)

    try:
        while True:
            for future in list(preparations):
                if not future.done():
                    continue
                scene, index, identities, budgets = preparations.pop(future)
                result = future.result()
                if result['status'] != 'ready':
                    preparation_failures.append({'scene_id': scene['scene_id'], 'station_index': index, **result})
                    if result.get('failure_scope') == 'shared':
                        failure = result
                        stop.set()
                    continue
                prepared = result['prepared']
                candidates = grasp_candidates(scene['task'], assets_dir)
                query_id = f'{identities[0].attempt_id}-batch'
                request = PlanningRequest(query_id, scene['scene_path'], identities, prepared.state,
                    'preclose', budgets, {'task': scene['task'], 'robot': scene['robot_config'],
                        'config': config, 'assets_dir': assets_dir, 'root': root,
                        'candidates': candidates, 'preparation_path': prepared.path,
                        'reuse_planners': resources.reuse_planners})
                batches[query_id] = (scene, index, prepared, budgets, candidates)
                pool.submit(request)

            for future in list(recordings):
                if future.done():
                    scene, index, identity = recordings.pop(future)
                    save(scene, index, identity, future.result())

            while True:
                try:
                    worker, request = requests.get_nowait()
                except Empty:
                    break
                routes[request.query_id] = worker
                pool.submit(request)

            while True:
                completed = pool.poll()
                if completed is None:
                    break
                request, responses = completed
                gpu_active_s += responses[0].planning_s
                if request.stage == 'pose':
                    replies[routes.pop(request.query_id)].put(responses[0])
                else:
                    scene, index, prepared, budgets, candidates = batches.pop(request.query_id)
                    for response, budget in zip(responses, budgets):
                        if response.status == 'success':
                            ready.append((scene, index, prepared, response, budget, candidates))
                        else:
                            record_later(scene, index, prepared, response, budget)
            if pool.failure:
                failure = pool.failure
                stop.set()

            while True:
                try:
                    receive_cpu(results.get_nowait())
                except Empty:
                    break
            if stop.is_set():
                break
            for worker in list(active):
                if cpus[worker].exitcode is not None:
                    failure = {'status': 'infrastructure_error', 'failure_scope': 'shared',
                               'reason': f'CPU worker {worker} exited with code {cpus[worker].exitcode}'}
                    scene, index, _, response, _, _ = active.pop(worker)
                    save(scene, index, response.identity, {**failure, 'rollout_id': response.identity.attempt_id,
                                                         'recording_complete': False})
            if failure:
                break

            while idle and ready:
                worker = idle.popleft()
                active[worker] = ready.popleft()
                jobs[worker].put(active[worker])

            buffered = (len(active) + len(ready) + len(recordings)
                        + sum(len(job[2]) for job in preparations.values())
                        + sum(len(job[3]) for job in batches.values()))
            while (not exhausted and len(preparations) < resources.preparation_workers
                   and buffered + config.trials_per_station <= capacity):
                job = next(units, None)
                if job is None:
                    exhausted = True
                    break
                scene, index, identities, budgets = job
                future = prep.submit(prepare_job, scene, index, config, root)
                preparations[future] = job
                buffered += len(identities)
            pool.dispatch()
            peaks['cpu_trials'] = max(peaks['cpu_trials'], len(active))
            peaks['gpu_queries'] = max(peaks['gpu_queries'], len(pool.active))
            peaks['preparations'] = max(peaks['preparations'], len(preparations))
            if exhausted and not (preparations or batches or recordings or ready or active):
                break
            # Wait briefly on actual result traffic while retaining responsive RPC dispatch.
            try:
                receive_cpu(results.get(timeout=.01))
            except Empty:
                pass
    finally:
        stop.set()
        if failure or active or pool.active:
            failure = failure or {'status': 'infrastructure_error', 'failure_scope': 'shared',
                                  'reason': 'calibration interrupted'}
            pool.failure = failure
            for worker, job in active.items():
                identity = job[3].identity
                replies[worker].put(PlanningResponse(f'{identity.attempt_id}-stop', identity,
                    'infrastructure_error', 0., job[4].plan_count, diagnostics=failure))
            while ready:
                scene, index, prepared, response, budget, _ = ready.popleft()
                record_later(scene, index, prepared, replace(response, status='infrastructure_error',
                                                            diagnostics=failure), budget)
        while active:
            try:
                receive_cpu(results.get(timeout=.1))
            except Empty:
                for worker in list(active):
                    if cpus[worker].exitcode is not None:
                        scene, index, _, response, _, _ = active.pop(worker)
                        save(scene, index, response.identity, {'status': 'infrastructure_error',
                            'failure_scope': 'shared', 'reason': 'execution worker exited',
                            'rollout_id': response.identity.attempt_id, 'recording_complete': False})
        for future, (scene, index, identity) in recordings.items():
            save(scene, index, identity, future.result())
        prep.shutdown(wait=True, cancel_futures=bool(failure))
        for queue in jobs:
            queue.put(None)
        if failure:
            while any(process.is_alive() for process in cpus):
                try:
                    requests.get(timeout=.1)
                except Empty:
                    pass
        for process in cpus:
            process.join()
        pool.close()
        for queue in [requests, results, *jobs, *replies]:
            if failure:
                queue.cancel_join_thread()
            queue.close()
            if not failure:
                queue.join_thread()
        if original_cuda is None:
            os.environ.pop('CUDA_VISIBLE_DEVICES', None)
        else:
            os.environ['CUDA_VISIBLE_DEVICES'] = original_cuda
    return trials, {'planned_trials': planned, 'recorded_trials': len(trials),
        'preparation_failures': preparation_failures, 'failure': failure, 'peaks': peaks,
        'gpu_active_s': gpu_active_s, 'elapsed_s': time.monotonic() - started}


def diagnose_stations(scenes, trials, assets_dir, config, root, gpu_ids, *, source_root=None,
                      allow_probes=True, service_type=None):
    """Parent-owned diagnosis writer; reuse trials and lazily lease the existing GPU pool."""
    from collections import Counter
    from contextlib import closing
    from .collection import _shared_failure
    from ..data.evaluation.diagnosis import diagnose_task
    from ..data.evaluation.diagnosis_inputs import PreparedMeasurements, planning_evidence
    from ..data.evaluation.selection import mode_calibration
    from ..runtime.simulation import Simulation
    from ..scenes.mode_config import ModeConfig
    from ..utils.io import read_json, write_json

    root = Path(root)
    source_root = root if source_root is None else Path(source_root)
    settings = ModeConfig(**read_json(root / 'config.json')['mode_settings'])
    diagnoses, label_counts = [], Counter()
    probe_count = 0
    pool, failure = None, None
    if (root / 'diagnosis.jsonl').exists():
        raise FileExistsError(root / 'diagnosis.jsonl')
    (root / 'diagnosis.jsonl').touch()
    try:
        for scene in scenes:
            current = [row for row in trials if row['scene_id'] == scene['scene_id']]
            binding = {'scene_id': scene['scene_id'], 'task_id': scene['task']['task_id']}
            calibration = mode_calibration(scene['stations'], current, binding, config.operation_backend)
            measurements, planning, probes = {}, {}, {}
            path = lambda a, b: {'status': 'unresolved', 'reason': 'measurements_not_available'}
            if calibration['task_eligibility'] == 'eligible':
                planning = planning_evidence(source_root, current)
                with closing(Simulation.from_snapshot(scene['scene_path'], scene['robot_config'],
                             target=scene['task']['target_body'])) as sim:
                    prepared = PreparedMeasurements(sim, scene, current, planning, config,
                        min_reference_pixels=read_json(root / 'config.json')['mode_visibility']['min_reference_pixels'])
                    if allow_probes and failure is None:
                        measurements = prepared.measure()
                    path = prepared.path
                    preliminary = diagnose_task(scene, calibration, current, measurements, planning, probes, path, settings)
                    for candidate in preliminary['s0_results']:
                        relation = candidate['labels']['reachability']
                        if relation['status'] != 'unresolved' or not allow_probes or failure is not None:
                            continue
                        if not settings.max_ik_probes_per_s0:
                            continue
                        identity = candidate['s0']
                        state = prepared.states.get(identity)
                        attempts = [attempt for trial in current if trial['station_id'] == identity
                                    for attempt in planning.get(trial['rollout_id'], {}).get('attempts', [])]
                        attempt = next((a for a in attempts if a.get('grasp_inputs_ref')), None)
                        if state is None or attempt is None:
                            probes[identity] = {'status': 'unresolved', 'reason': 'missing_probe_inputs'}
                            continue
                        candidates_ref = Path(attempt['grasp_inputs_ref']).with_name('grasp_candidates.json')
                        if not candidates_ref.is_file():
                            probes[identity] = {'status': 'unresolved', 'reason': 'missing_grasp_candidate_binding'}
                            continue
                        lane = read_json(candidates_ref)['lanes'][attempt.get('batch_slot', 0)]
                        station_index = next(i for i, s in enumerate(scene['stations']) if s['station_id'] == identity)
                        probe_identity = TrialIdentity(scene['scene_id'], station_index, -1,
                            f'probe-{scene["scene_id"]}-{identity}', config.seed + 1000000 + station_index)
                        request = PlanningRequest(probe_identity.attempt_id, scene['scene_path'], [probe_identity], state,
                            'diagnostic_ik', [TrialBudget(settings.probe_timeout_s)],
                            {'task': scene['task'], 'robot': scene['robot_config'], 'config': config,
                             'assets_dir': assets_dir, 'root': root, 'candidates': lane['candidates'],
                             'trial_refs': [str((source_root / trial['planning_ref']).resolve())
                                            for trial in current if trial['station_id'] == identity and trial.get('planning_ref')]})
                        if pool is None:
                            pool = PlanningPool(gpu_ids, **({'service_type': service_type} if service_type else {}))
                        pool.submit(request)
                        probe_count += 1
                        pool.dispatch()
                        response = None
                        timed_out = False
                        while response is None and pool.failure is None:
                            observed = pool.poll(.1)
                            if observed is not None:
                                response = observed[1][0]
                            marker = root / 'planning' / request.query_id / 'query.json'
                            if response is None and marker.is_file():
                                active = time.monotonic() - read_json(marker)['started_at']
                                if active > settings.probe_timeout_s:
                                    pool.failure = {'status': 'timeout', 'reason': 'diagnostic_ik_active_budget'}
                                    pool.close()
                                    pool = None
                                    probe = {'status': 'unresolved', 'reason': 'diagnostic_ik_active_budget',
                                        'feasible_ratio': None, 'active_s': active,
                                        'limit_s': settings.probe_timeout_s, 'diagnostic_only': True,
                                        'result_ref': str(marker.with_name('ik_probe.json').resolve())}
                                    write_json(marker.with_name('ik_probe.json'), probe)
                                    probes[identity] = probe
                                    timed_out = True
                                    break
                        if timed_out:
                            continue
                        if response is None:
                            failure = pool.failure
                            probes[identity] = {'status': 'unresolved', 'reason': failure['reason']}
                        elif response.status != 'success':
                            probes[identity] = {'status': 'unresolved', 'reason': response.diagnostics.get('reason', response.status)}
                            if response.status == 'infrastructure_error':
                                failure = response.diagnostics
                        else:
                            probe = response.plan['probe']
                            probes[identity] = {k: v for k, v in probe.items() if k not in ('per_seed_success', 'seed_indices', 'sampler_seeds')}
                            probes[identity]['result_ref'] = str((root / 'planning' / request.query_id / 'ik_probe.json').resolve())
            diagnosis = diagnose_task(scene, calibration, current, measurements, planning, probes, path, settings)
            diagnosis.update(trials_ref=str((source_root / 'trials.jsonl').resolve()),
                calibration_ref=str(source_root.resolve()), station_measurements=measurements, probes=probes)
            diagnoses.append(diagnosis)
            label_counts.update(label for row in diagnosis['s0_results'] for label in row['final_labels'])
            append_row(root / 'diagnosis.jsonl', diagnosis)
    finally:
        if pool is not None:
            pool.close()
    return diagnoses, {'tasks': len(diagnoses), 'eligibility': dict(Counter(r['task_eligibility'] for r in diagnoses)),
        'intent_hit': dict(Counter(str(r['intent_hit']) for r in diagnoses)), 'label_counts': dict(label_counts),
        'probe_count': probe_count, 'failure': failure}
