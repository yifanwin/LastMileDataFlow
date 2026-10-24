"""CPU-side planner interface: numerical RPCs and existing trajectory tracking."""
import time

from .waypoints import PlanResult
from ..runtime.execution.protocol import PlanningRequest, SimulationState
from ..utils.io import write_json


class PlanningClient:
    def __init__(self, worker, requests, responses, identity, budget, first, inputs, *, stop=None):
        self.worker, self.requests, self.responses = worker, requests, responses
        self.identity, self.budget, self.first, self.inputs = identity, budget, first, inputs
        self.serial = 0
        self.stop = stop
        self.ctx = None
        self.accept(first)
        self.budget.queue_s += time.monotonic() - first.completed_at

    def accept(self, response):
        self.budget.planning_s += response.planning_s
        self.budget.queue_s += response.queue_s
        self.budget.plan_count = response.plan_count

    def bind(self, ctx):
        self.ctx = ctx
        ctx.stop = self.stop
        ctx.planner_factory = self.factory
        ctx.plans.append({'phase': 'native_batch_grasp', 'query_id': self.first.query_id,
                          'seed': self.identity.seed, **self.first.plan['diagnostics'],
                          **self.first.diagnostics})
        write_json(ctx.recorder.path / 'planning.json', ctx.plans)

    def factory(self, sim, config, side, path, *, workspace):
        return RemotePlanner(self, sim, config, side, path, workspace)

    def query(self, planner, goal):
        self.serial += 1
        request = PlanningRequest(f'{self.identity.attempt_id}-q{self.serial}',
            self.inputs['model_dir'], [self.identity], SimulationState.capture(planner.sim),
            'pose', [self.budget], {**self.inputs, 'side': planner.side,
                'phase': planner.single_phase, 'attached': planner.attached, 'goal': goal,
                'recording_step': self.ctx.recorder.steps})
        self.requests.put((self.worker, request))
        response = self.responses.get()
        self.accept(response)
        self.ctx.plan_count = self.budget.plan_count
        if response.status == 'infrastructure_error':
            from .curobo_v2 import PlannerUnavailable
            raise PlannerUnavailable(response.diagnostics['reason'])
        if response.status == 'timeout' and 'result' not in response.plan:
            raise TimeoutError(response.diagnostics.get('reason', 'attempt_active_budget'))
        if response.status not in ('success', 'planning_no_solution', 'timeout'):
            raise RuntimeError(response.diagnostics['reason'])
        if 'attached_object' in response.plan:
            write_json(self.ctx.recorder.path / 'attached_object.json', response.plan['attached_object'])
        if 'result' not in response.plan:
            return PlanResult('no_solution', tuple(planner.names), (), response.diagnostics)
        result = response.plan['result']
        result['diagnostics'].update(query_id=response.query_id, **response.diagnostics,
                                     planning_s=response.planning_s, queue_s=response.queue_s)
        return PlanResult(**result)

    def close(self):
        self.ctx = None


class RemotePlanner:
    def __init__(self, client, sim, config, side, path, workspace):
        self.client, self.sim, self.side, self.path = client, sim, side, path
        self.model = sim.robot.planning_model(sim, config, side, path, workspace)
        self.names, self.base = self.model.names, self.model.base
        self.h_step_limit = self.model.h_step_limit
        self.attached = False
        self.single_phase = 'pose'

    def base_target_world(self, point):
        return self.model.base_target_world(point)

    def grasp(self, matrices, on_query, *, lift=True):
        response = self.client.first
        self.client.first = None
        plan = response.plan
        write_json(self.path / 'grasp_result.json', {'query_id': response.query_id,
            'success': plan['success'], 'stages': plan['stages'], **plan['diagnostics']})
        return plan['success'], plan['index'], plan['stages'], plan['diagnostics']

    def attach_target_geometry(self):
        self.attached = True
        return {'physics_attachment': False, 'fitting': 'requested_on_next_planning_query'}

    def refresh_world(self, path):
        # Every RPC carries the current measured scene, including open-joint motion.
        pass

    def plan(self, goal):
        return self.client.query(self, goal)

    def detach(self):
        self.attached = False

    def close(self):
        self.sim = self.model.sim = None
