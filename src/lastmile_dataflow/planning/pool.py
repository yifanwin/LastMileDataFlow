"""Run-owned GPU processes; one native query per device, post-close work first."""
from contextlib import closing
from dataclasses import asdict, replace
import heapq
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import time
from types import SimpleNamespace

from ..runtime.execution.protocol import PlanningResponse
from ..utils.io import write_json


class PlanningService:
    """GPU-owned planning views; no state is written to a CPU execution instance."""
    def __init__(self):
        from ..runtime.simulation import SnapshotCache
        from .curobo.planner import PlannerResources
        self.scenes = SnapshotCache()
        self.planners = PlannerResources()

    def baseline(self, request):
        return self.scenes.get(request.model_dir, request.inputs['robot'],
                               request.inputs['task']['target_body'])

    def execute(self, request):
        from .curobo.planner import V2Planner, bind_batch_seeds
        from ..runtime.execution.context import clone_sim, planner_options, OperationFailure
        from ..runtime.execution.manipulation import candidate_pool, grasp_goals

        started = time.monotonic()
        queue_s = started - request.enqueued_at
        inputs = request.inputs
        task, config = inputs['task'], inputs['config']
        directory = Path(inputs['root']) / 'planning' / request.query_id
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / 'query.json', {'query_id': request.query_id,
            'started_at': started,
            'identities': [asdict(identity) for identity in request.identities],
            'budgets': [asdict(budget) for budget in request.budgets], 'stage': request.stage,
            'model_dir': request.model_dir, 'preparation_path': inputs.get('preparation_path'),
            'recording_step': inputs.get('recording_step')})
        baseline = self.baseline(request)
        if request.stage == 'diagnostic_ik':
            return self.diagnostic_ik(request, baseline, directory, started, queue_s)
        max_plans = config.max_open_plans if task['operation'] == 'open' else config.max_operation_plans

        def query(phase):
            for budget in request.budgets:
                budget.check()
                if budget.plan_count >= max_plans:
                    raise OperationFailure('planning_budget_no_solution')
                budget.plan_count += 1

        with closing(clone_sim(baseline, target=task['target_body'], handle=task.get('handle_body'))) as sim:
            request.state.initialize(sim)
            seeds = [identity.seed for identity in request.identities]
            side = inputs.get('side', sim.robot.arms[0])
            closed = request.stage == 'pose'
            options = replace(config, operation_base_mode='fixed') if closed and sim.robot.lock_base_after_close else config
            options = planner_options(options, inputs['assets_dir'], seeds[0])
            arguments = dict(workspace=(task['anchor_world'], config.radius_m), batch_size=len(seeds))
            lease = (self.planners.acquire(sim, options, side, directory, **arguments)
                     if inputs.get('reuse_planners', True) else
                     closing(V2Planner(sim, options, side, directory, **arguments)))
            with lease as planner:
                if not closed:
                    context = SimpleNamespace(sim=sim, config=config)
                    pools = [candidate_pool(context, inputs['candidates'], seed, None) for seed in seeds]
                    if not pools[0]:
                        raise OperationFailure('no_grasp_candidates')
                    goals = [grasp_goals(sim, config, pool, retreat=task['operation'] == 'open') for pool in pools]
                    write_json(directory / 'grasp_candidates.json', {'lanes': [
                        {'identity': asdict(identity), 'seed': identity.seed, 'candidates': pool}
                        for identity, pool in zip(request.identities, pools)]})
                    lanes = planner.grasp_batch(goals, seeds, query)
                    for lane in lanes:
                        lane.update(side=side, joint_names=list(planner.names), reference_world=planner.base.tolist())
                else:
                    bind_batch_seeds(planner.motion, seeds)
                    planner.single_phase = inputs['phase']
                    attached = None
                    if inputs.get('attached'):
                        attached = planner.attach_target_geometry()
                    planner.on_query = query
                    result = planner.plan(inputs['goal'])
                    lanes = [{'result': asdict(result), 'side': side}]
                    if attached is not None:
                        lanes[0]['attached_object'] = attached
        elapsed = time.monotonic() - started
        responses = []
        for index, (identity, lane) in enumerate(zip(request.identities, lanes)):
            lane.setdefault('diagnostics', {}).update(query_id=request.query_id,
                identity=asdict(identity), batch_slot=index,
                query_ref=str((directory / 'query.json').resolve()))
            if not closed:
                lane['diagnostics']['planning_state_ref'] = str(Path(inputs['preparation_path']) / 'state.npz')
            success = lane['result']['status'] == 'success' if closed else lane['success']
            status = 'success' if success else 'planning_no_solution'
            if request.budgets[index].remaining_s <= elapsed:
                status = 'timeout'
            responses.append(PlanningResponse(request.query_id, identity, status, elapsed,
                request.budgets[index].plan_count, lane,
                {'stage': request.stage, 'query_path': str(directory)}, queue_s))
        return responses

    def diagnostic_ik(self, request, baseline, directory, started, queue_s):
        from ..runtime.execution.context import clone_sim, planner_options
        from ..runtime.execution.manipulation import grasp_goals
        from ..scenes.mode_config import load_mode_config

        if len(request.identities) != 1:
            raise ValueError('diagnostic IK owns one S0 identity, separate from raw trials')
        inputs = request.inputs
        config, task = inputs['config'], inputs['task']
        settings = load_mode_config(config.mode_config)
        identity, budget = request.identities[0], request.budgets[0]
        limit = min(settings.probe_timeout_s, budget.limit_s)
        candidates = inputs['candidates'][:settings.probe_grasps]
        write_json(directory / 'grasp_candidates.json', {'identity': asdict(identity),
            'candidates': candidates, 'trial_refs': inputs['trial_refs'], 'diagnostic_only': True})
        evidence = {'status': 'unresolved', 'reason': 'no_diagnostic_grasps'}
        if candidates:
            with closing(clone_sim(baseline, target=task['target_body'], handle=task.get('handle_body'))) as sim:
                request.state.initialize(sim)
                options = planner_options(replace(config, num_ik_seeds=settings.probe_ik_seeds),
                                          inputs['assets_dir'], identity.seed)
                with self.planners.acquire(sim, options, sim.robot.arms[0], directory,
                        workspace=(task['anchor_world'], config.radius_m),
                        batch_size=len(candidates), ik_only=True) as planner:
                    if time.monotonic() - started >= limit:
                        evidence = {'status': 'unresolved', 'reason': 'probe_active_budget'}
                    else:
                        goals = grasp_goals(sim, config, candidates, retreat=task['operation'] == 'open')
                        evidence = planner.diagnostic_ik(goals, seed=identity.seed,
                                                        seeds_per_grasp=settings.probe_ik_seeds)
        elapsed = time.monotonic() - started
        if elapsed > limit:
            evidence.update(status='unresolved', reason='probe_active_budget', feasible_ratio=None)
        evidence.update(active_s=elapsed, queue_s=queue_s, limit_s=limit, diagnostic_only=True,
                        query_ref=str((directory / 'query.json').resolve()), trial_refs=inputs['trial_refs'])
        write_json(directory / 'ik_probe.json', evidence)
        return [PlanningResponse(request.query_id, identity, 'success', elapsed,
            budget.plan_count, {'probe': evidence}, {'stage': request.stage, 'query_path': str(directory)}, queue_s)]

    def close(self):
        self.planners.close()
        self.scenes.close()


def _planning_worker(index, gpu, requests, results, service_type):
    os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu), OMP_NUM_THREADS='1',
                      OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    from ..runtime.execution.lifecycle import classify_episode_exception
    from ..runtime.execution.context import OperationFailure

    with closing(service_type()) as service:
        while True:
            request = requests.get()
            if request is None:
                return
            started = time.monotonic()
            try:
                responses = service.execute(request)
            except Exception as exc:
                if isinstance(exc, (TimeoutError, OperationFailure)):
                    failure = {'status': 'timeout' if isinstance(exc, TimeoutError) else 'planning_no_solution',
                               'reason': str(exc), 'failure_scope': 'episode'}
                else:
                    failure = classify_episode_exception(exc, 'planning')
                responses = [PlanningResponse(request.query_id, identity, failure['status'],
                    time.monotonic() - started, budget.plan_count, diagnostics=failure,
                    queue_s=started - request.enqueued_at)
                    for identity, budget in zip(request.identities, request.budgets)]
            for response in responses:
                response.diagnostics.update(worker=index, gpu=gpu, pid=os.getpid(),
                                            cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES'])
            results.put((index, responses))
            if any(response.status == 'infrastructure_error' for response in responses):
                return


class PlanningPool:
    def __init__(self, gpu_ids, *, service_type=PlanningService, workers_per_gpu=1):
        values = (list(workers_per_gpu) if isinstance(workers_per_gpu, (list, tuple))
                  else [workers_per_gpu])
        if len(values) == 1:
            values = values * len(gpu_ids)
        if len(values) != len(gpu_ids) or any(
                not isinstance(value, int) or not 1 <= value <= 4 for value in values):
            raise ValueError('planning workers per GPU must be one value or one 1-4 value per GPU')
        self.context = multiprocessing.get_context('spawn')
        self.results = self.context.Queue()
        self.workers_per_gpu = tuple(values)
        self.worker_gpu = [gpu for gpu, count in zip(gpu_ids, values) for _ in range(count)]
        self.requests = [self.context.Queue() for _ in self.worker_gpu]
        self.processes = [self.context.Process(target=_planning_worker,
            args=(index, self.worker_gpu[index], self.requests[index], self.results, service_type))
            for index in range(len(self.requests))]
        self.pending = []
        self.active = {}
        self.models = {}
        self.serial = 0
        self.failure = None
        for process in self.processes:
            process.start()

    def submit(self, request):
        priority = 0 if request.stage == 'pose' else 2 if request.stage == 'diagnostic_ik' else 1
        heapq.heappush(self.pending, (priority, self.serial, request))
        self.serial += 1

    def dispatch(self):
        idle = [index for index in range(len(self.processes)) if index not in self.active]
        while idle and self.pending and self.failure is None:
            _, _, request = heapq.heappop(self.pending)
            index = next((i for i in idle if self.models.get(i) == request.model_dir), idle[0])
            idle.remove(index)
            self.requests[index].put(request)
            self.active[index] = request
            self.models[index] = request.model_dir

    def poll(self, timeout=0.):
        try:
            index, responses = self.results.get(timeout=timeout)
        except Empty:
            for index, process in enumerate(self.processes):
                if process.exitcode is not None and self.failure is None:
                    self.failure = {'status': 'infrastructure_error', 'failure_scope': 'shared',
                                    'reason': f'GPU worker {index} exited with code {process.exitcode}'}
            return None
        request = self.active.pop(index)
        shared = next((r for r in responses if r.status == 'infrastructure_error'), None)
        if shared is not None:
            self.failure = shared.diagnostics
        return request, responses

    def close(self):
        for index, process in enumerate(self.processes):
            if self.failure and process.is_alive():
                process.terminate()
            else:
                self.requests[index].put(None)
        for process in self.processes:
            process.join()
        for queue in [*self.requests, self.results]:
            if self.failure:
                queue.cancel_join_thread()
            queue.close()
            if not self.failure:
                queue.join_thread()
