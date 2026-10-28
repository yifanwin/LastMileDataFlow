"""Spawned planning processes and parent scheduling; no CUDA acceptance claims."""
import os
import time
import unittest

import numpy as np

from lastmile_dataflow.planning.pool import PlanningPool
from lastmile_dataflow.runtime.execution.protocol import (
    PlanningRequest, PlanningResponse, SimulationState, TrialBudget, TrialIdentity,
)


class FixturePlanningService:
    def execute(self, request):
        if request.inputs.get('fault'):
            raise MemoryError('synthetic shared resource failure')
        return [PlanningResponse(request.query_id, identity, 'success', .25, budget.plan_count + 1,
            diagnostics={'pid': os.getpid(), 'gpu': os.environ['CUDA_VISIBLE_DEVICES']})
            for identity, budget in zip(request.identities, request.budgets)]

    def close(self):
        pass


def request(identity, stage='preclose', *, fault=False):
    return PlanningRequest(identity, 'fixture-model',
        [TrialIdentity('scene', 0, index, f'{identity}-{index}', 42 + index) for index in range(3)],
        SimulationState(np.zeros(1), 0, {}, 0.), stage,
        [TrialBudget(plan_count=2) for _ in range(3)], {'fault': fault})


def receive(pool):
    deadline = time.monotonic() + 15.
    while time.monotonic() < deadline:
        result = pool.poll(.1)
        if result is not None:
            return result
        if pool.failure:
            raise AssertionError(pool.failure)
    raise AssertionError('planning fixture did not return')


class PlanningPoolTests(unittest.TestCase):
    def test_one_resident_worker_prioritizes_pose_then_fifo_and_preserves_identities(self):
        pool = PlanningPool([2], service_type=FixturePlanningService)
        self.addCleanup(pool.close)
        for item in (request('first'), request('second'), request('closed', 'pose')):
            pool.submit(item)
        observed, pids = [], []
        for _ in range(3):
            pool.dispatch()
            item, responses = receive(pool)
            observed.append(item.query_id)
            pids.append(responses[0].diagnostics['pid'])
            self.assertEqual([r.identity for r in responses], item.identities)
            self.assertEqual([r.plan_count for r in responses], [3, 3, 3])
            self.assertTrue(all(r.diagnostics['gpu'] == 2 for r in responses))
            self.assertTrue(all(r.diagnostics['cuda_visible_devices'] == '2' for r in responses))
        self.assertEqual(observed, ['closed', 'first', 'second'])
        self.assertEqual(len(set(pids)), 1)
        self.assertNotEqual(pids[0], os.getpid())

    def test_multiple_devices_own_distinct_processes_using_the_same_protocol(self):
        pool = PlanningPool([2, 5], service_type=FixturePlanningService)
        self.addCleanup(pool.close)
        pool.submit(request('one'))
        pool.submit(request('two'))
        pool.dispatch()
        self.assertEqual(len(pool.active), 2)
        results = [receive(pool), receive(pool)]
        self.assertEqual({r[1][0].diagnostics['gpu'] for r in results}, {2, 5})
        self.assertEqual(len({r[1][0].diagnostics['pid'] for r in results}), 2)

    def test_shared_failure_stops_dispatch_and_preserves_the_returned_failure(self):
        pool = PlanningPool([0], service_type=FixturePlanningService)
        self.addCleanup(pool.close)
        pool.submit(request('failed', fault=True))
        pool.submit(request('not-dispatched'))
        pool.dispatch()
        _, responses = receive(pool)
        self.assertTrue(all(r.status == 'infrastructure_error' for r in responses))
        self.assertEqual(pool.failure['failure_scope'], 'shared')
        pool.dispatch()
        self.assertEqual(len(pool.pending), 1)
        self.assertEqual(pool.active, {})

    def test_resident_service_uses_the_current_planner_module(self):
        from lastmile_dataflow.planning.curobo.planner import PlannerResources
        from lastmile_dataflow.planning.pool import PlanningService

        service = PlanningService()
        self.addCleanup(service.close)
        self.assertIsInstance(service.planners, PlannerResources)
