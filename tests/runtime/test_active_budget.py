"""A trial carries native planning cost and counts across CPU/GPU transfers."""
from dataclasses import asdict
import pickle
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.planning.remote import PlanningClient
from lastmile_dataflow.runtime.execution.checks import timed
from lastmile_dataflow.runtime.execution.context import ContinuousContext
from lastmile_dataflow.runtime.execution.protocol import PlanningResponse, TrialBudget, TrialIdentity
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from tests.helpers import DatasetTestCase


class SerializedQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(pickle.loads(pickle.dumps(item)))


class ActiveBudgetTests(DatasetTestCase):
    def context(self, budget):
        recorder = AttemptRecorder(self.root, {'collection': asdict(CollectionConfig(
            record_video=False, record_images=False))}, attempt_id='budget')
        recorder.bind_simulation(self.sim)
        ctx = ContinuousContext(self.sim, recorder, ExecutionConfig(), budget=budget)
        self.addCleanup(ctx.finish)
        return ctx

    def test_physical_time_is_active_but_queue_and_recording_time_are_not(self):
        budget = TrialBudget(limit_s=5., planning_s=2., queue_s=1000., plan_count=3)
        ctx = self.context(budget)
        with patch('time.monotonic', side_effect=[100., 100.125]):
            ctx.tick(self.sim.robot.neutral_action())
        self.assertEqual(budget.execution_s, .125)
        self.assertEqual(ctx.plan_count, 3)
        self.assertEqual(ctx.recorder.steps, 1)
        with patch('time.monotonic', side_effect=[100., 200.]):
            with timed(ctx, 'recording'):
                pass
        self.assertEqual(budget.execution_s, .125)
        with patch('time.monotonic', return_value=1e12):
            ctx.deadline()
        budget.planning_s += 3.
        with self.assertRaisesRegex(TimeoutError, 'attempt_active_budget'):
            ctx.deadline()

    def test_measured_rpc_preserves_trial_state_and_first_batch_cost(self):
        identity = TrialIdentity('scene', 2, 1, 'r7', 49)
        budget = TrialBudget(limit_s=300.)
        first = PlanningResponse('batch2', identity, 'success', 2., 3,
                                  {'diagnostics': {}}, queue_s=50.)
        requests, responses = SerializedQueue(), Queue()
        client = PlanningClient(4, requests, responses, identity, budget, first,
                                {'model_dir': 'fixed-scene'})
        self.addCleanup(client.close)
        ctx = self.context(budget)
        client.bind(ctx)
        ctx.tick(self.sim.robot.neutral_action())
        measured = self.sim.state_vector().copy()
        responses.put(PlanningResponse('r7-q1', identity, 'success', 1., 4,
            {'result': {'status': 'success', 'joint_names': ('a', 'b'),
                        'positions': ((.1, .2),), 'diagnostics': {}}}, queue_s=70.))
        planner = SimpleNamespace(sim=self.sim, side='right', single_phase='approach_contact',
                                  attached=False, names=('a', 'b'))
        plan = client.query(planner, np.eye(4))
        worker, request = requests.items[0]
        self.assertEqual(worker, 4)
        self.assertEqual(request.identities, [identity])
        self.assertEqual(request.query_id, 'r7-q1')
        self.assertEqual(request.budgets[0].plan_count, 3)
        self.assertEqual(request.inputs['phase'], 'approach_contact')
        self.assertEqual(request.inputs['recording_step'], 1)
        np.testing.assert_array_equal(request.state.integration, measured)
        np.testing.assert_array_equal(self.sim.state_vector(), measured)
        self.assertTrue(self.sim.started)
        self.assertEqual(budget.planning_s, 3.)
        self.assertEqual(budget.plan_count, 4)
        self.assertGreaterEqual(budget.queue_s, 120.)
        self.assertEqual(plan.diagnostics['query_id'], 'r7-q1')
        ctx.deadline()
