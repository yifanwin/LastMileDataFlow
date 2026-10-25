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
        from .curobo_v2 import PlannerResources
        self.scenes = SnapshotCache()
        self.planners = PlannerResources()

    def baseline(self, request):
        return self.scenes.get(request.model_dir, request.inputs['robot'],
                               request.inputs['task']['target_body'])

    def execute(self, request):
        from .curobo_v2 import V2Planner, bind_batch_seeds
        from ..runtime.execution.context import clone_sim, planner_options, OperationFailure
        from ..runtime.execution.manipulation import candidate_pool, grasp_goals

        started = time.monotonic()
        queue_s = started - request.enqueued_at
        inputs = request.inputs
        task, config = inputs['task'], inputs['config']
        baseline = self.baseline(request)
        directory = Path(inputs['root']) / 'planning' / request.query_id
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / 'query.json', {'query_id': request.query_id,
            'identities': [asdict(identity) for identity in request.identities],
            'budgets': [asdict(budget) for budget in request.budgets], 'stage': request.stage,
            'model_dir': request.model_dir, 'preparation_path': inputs.get('preparation_path'),
            'recording_step': inputs.get('recording_step')})
        max_plans = config.max_open_plans if task['operation'] == 'open' else config.max_operation_plans

        def query(phase):
            for budget in request.budgets:
                budget.check()
                if budget.plan_count >= max_plans:
                    raise OperationFailure('planning_budget_no_solution')
                budget.plan_count += 1

        with closing(clone_sim(baseline, target=task['target_body'])) as sim:
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
                    goals = [grasp_goals(sim, config, pool) for pool in pools]
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
            success = lane['result']['status'] == 'success' if closed else lane['success']
            status = 'success' if success else 'planning_no_solution'
            if request.budgets[index].remaining_s <= elapsed:
                status = 'timeout'
            responses.append(PlanningResponse(request.query_id, identity, status, elapsed,
                request.budgets[index].plan_count, lane,
                {'stage': request.stage, 'query_path': str(directory)}, queue_s))
        return responses

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
    def __init__(self, gpu_ids, *, service_type=PlanningService):
        self.context = multiprocessing.get_context('spawn')
        self.results = self.context.Queue()
        self.requests = [self.context.Queue() for _ in gpu_ids]
        self.processes = [self.context.Process(target=_planning_worker,
            args=(index, gpu, self.requests[index], self.results, service_type))
            for index, gpu in enumerate(gpu_ids)]
        self.pending = []
        self.active = {}
        self.models = {}
        self.serial = 0
        self.failure = None
        for process in self.processes:
            process.start()

    def submit(self, request):
        priority = 0 if request.stage == 'pose' else 1
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
