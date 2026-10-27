"""Native phase/lane identity, retries and fallback exclusion without GPU solves."""
from types import SimpleNamespace
import unittest
import torch

from lastmile_dataflow.planning.evidence import lane_success, solver_evidence, phase_evidence
from lastmile_dataflow.planning.curobo.context import PlannerContext


def query(index, phase, values, fov=None):
    return {'query_index': index, 'phase': phase, 'lane_success': values,
            'fov_rejected_lanes': fov or [False] * len(values)}


def solve(index, phase, kind, values, count=3, query_index=None):
    return solver_evidence(SimpleNamespace(success=torch.tensor(values)), kind, phase,
                           index if query_index is None else query_index, index, count)


class PlanningEvidenceTests(unittest.TestCase):
    def test_lane_result_is_not_batch_any(self):
        self.assertEqual(lane_success(torch.tensor([[True, False], [False, False], [False, True]]), 3),
                         [True, False, True])
        self.assertEqual(lane_success(None, 3), [False, False, False])

    def test_failed_lane_cannot_claim_later_fallback_success(self):
        queries = [query(1, 'goalset', [False, True, True]),
                   query(2, 'pregrasp', [True, False, True]),
                   query(3, 'grasp', [True, True, True])]
        solves = [solve(1, 'goalset', 'ik', [[False], [True], [True]]),
                  solve(2, 'pregrasp', 'ik', [[True], [False], [True]]),
                  solve(3, 'grasp', 'ik', [[True], [True], [True]])]
        first = phase_evidence(queries, solves, 0)
        self.assertEqual([r['status'] for r in first], ['failure', 'not_applicable', 'not_applicable'])
        second = phase_evidence(queries, solves, 1)
        self.assertEqual([r['status'] for r in second], ['success', 'failure', 'not_applicable'])
        self.assertEqual(second[1]['failure_component'], 'ik')
        third = phase_evidence(queries, solves, 2)
        self.assertTrue(all(r['status'] == 'success' for r in third))

    def test_solver_retries_keep_explicit_query_mapping_and_unknown_denominator(self):
        queries = [query(1, 'goalset', [False, True, False])]
        solves = [solve(i, 'goalset', kind, values, query_index=1) for i, kind, values in (
            (1, 'ik', [[False], [False], [True]]),
            (2, 'ik', [[True], [True], [False]]),
            (3, 'trajopt', [[False], [True], [False]]),
            (4, 'ik', [[False], [True], [False]]),
            (5, 'trajopt', [[False], [False], [False]]))]
        failed = phase_evidence(queries, solves, 0)[0]
        self.assertTrue(failed['ik_found'])
        self.assertFalse(failed['trajopt_found'])
        self.assertEqual(failed['failure_component'], 'trajopt')
        self.assertEqual(failed['solver_calls'], [1, 2, 3, 4, 5])
        self.assertIsNone(failed['ik_feasible_ratio'])
        self.assertEqual(solves[0]['returned_shape'], [3, 1])

    def test_fov_rejection_is_separate_from_trajopt_failure(self):
        queries = [query(1, 'goalset', [False], [True])]
        solves = [solve(1, 'goalset', 'ik', [[True]], count=1),
                  solve(2, 'goalset', 'trajopt', [[True]], count=1, query_index=1)]
        observed = phase_evidence(queries, solves, 0)[0]
        self.assertEqual(observed['failure_component'], 'fov')
        self.assertTrue(observed['trajopt_found'])

    def test_missing_solver_observation_remains_unknown(self):
        observed = phase_evidence([query(1, 'goalset', [False])], [], 0)[0]
        self.assertEqual(observed['failure_component'], 'unknown')
        self.assertIsNone(observed['ik_found'])


class IKProbeEvidenceTests(unittest.TestCase):
    def result(self, selected=4):
        return SimpleNamespace(success=torch.tensor([[True, False, False, False], [False, False, True, False]])[:, :selected],
            debug_info={'seed_idx': torch.tensor([[2, 0, 3, 1], [7, 4, 6, 5]])[:, :selected]},
            optimized_seeds=torch.zeros((2, 4, 3)))

    def test_full_seed_permutation_proves_denominator(self):
        from lastmile_dataflow.planning.evidence import ik_probe_evidence
        observed = ik_probe_evidence(self.result(), 2, 4)
        self.assertEqual(observed['status'], 'measured')
        self.assertEqual(observed['observed_evaluation_count'], 8)
        self.assertEqual(observed['feasible_count'], 2)
        self.assertEqual(observed['feasible_ratio'], .25)

    def test_selected_subset_or_duplicate_seed_cannot_claim_full_ratio(self):
        from lastmile_dataflow.planning.evidence import ik_probe_evidence
        for result in (self.result(2), self.result()):
            if result.success.shape[1] == 4:
                result.debug_info['seed_idx'][0, 1] = 2
            observed = ik_probe_evidence(result, 2, 4)
            self.assertEqual(observed['status'], 'unresolved')
            self.assertIsNone(observed['feasible_ratio'])

    def test_diagnostic_ik_uses_one_goal_per_grasp_and_never_calls_motion_plan(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import Mock, patch
        from lastmile_dataflow.planning.curobo.planner import V2Planner
        planner = V2Planner.__new__(V2Planner)
        planner.motion = SimpleNamespace(ik_solver=SimpleNamespace(solve_pose=Mock(return_value=self.result())),
                                         plan_pose=Mock(), plan_grasp=Mock())
        planner.config = SimpleNamespace(head_fov_enabled=False)
        planner.query_log, planner.solver_log, planner.context = [], [], PlannerContext(target_checker=None)
        planner.batch_goals = Mock(side_effect=lambda values: values)
        planner.state = Mock(return_value=SimpleNamespace(clone=lambda: SimpleNamespace(dt=.1)))
        with tempfile.TemporaryDirectory() as directory:
            planner.path = Path(directory)
            with patch('lastmile_dataflow.planning.curobo.planner.bind_batch_seeds') as bind:
                observed = planner.diagnostic_ik([torch.eye(4).numpy()] * 2, seed=100, seeds_per_grasp=4)
                bind.assert_called_once_with(planner.motion, [100, 101])
            self.assertTrue((planner.path / 'ik_probe.json').is_file())
        self.assertEqual(observed['feasible_ratio'], .25)
        self.assertEqual([len(goals) for goals in planner.batch_goals.call_args.args[0]], [1, 1])
        self.assertEqual(planner.motion.ik_solver.solve_pose.call_args.kwargs['return_seeds'], 4)
        planner.motion.plan_pose.assert_not_called()
        planner.motion.plan_grasp.assert_not_called()
        self.assertFalse(planner.context.contact_allowed)


class ProbeServiceTests(unittest.TestCase):
    def execute_probe(self, directory, limit=60.):
        from contextlib import nullcontext
        from pathlib import Path
        import time
        from unittest.mock import Mock, patch
        from lastmile_dataflow.planning.pool import PlanningService
        from lastmile_dataflow.runtime.execution.protocol import PlanningRequest, TrialBudget, TrialIdentity
        from lastmile_dataflow.runtime.execution_config import ExecutionConfig
        identity = TrialIdentity('scene', 0, -1, 'probe-s0', 1000042)
        config = ExecutionConfig(trials_per_station=3, mode_config='configs/task_modes/mode_defaults.json',
                                 operation_base_mode='holonomic_joint_planning', local_base_radius_m=.35)
        inputs = {'config': config, 'task': {'operation': 'pick', 'target_body': 'target', 'anchor_world': [0., 0., 1.]},
                  'assets_dir': 'assets', 'candidates': [{'row': index} for index in range(17)],
                  'trial_refs': ['rollouts/r000000/planning.json']}
        request = PlanningRequest('probe-query', 'snapshot', [identity], Mock(), 'diagnostic_ik',
                                  [TrialBudget(limit_s=limit, plan_count=5)], inputs)
        planner = Mock()
        planner.diagnostic_ik.return_value = {'status': 'measured', 'feasible_ratio': 0., 'feasible_count': 0}
        service = PlanningService.__new__(PlanningService)
        service.planners = SimpleNamespace(acquire=Mock(return_value=nullcontext(planner)))
        sim = Mock(robot=SimpleNamespace(arms=['right']))
        with (patch('lastmile_dataflow.runtime.execution.context.clone_sim', return_value=sim),
              patch('lastmile_dataflow.runtime.execution.manipulation.grasp_goals', return_value=[object()] * 16)):
            response = service.diagnostic_ik(request, Mock(), Path(directory), time.monotonic(), .5)[0]
        return response, request, planner, service

    def test_separate_probe_identity_preserves_mobile_domain_and_raw_trial_count(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            response, request, planner, service = self.execute_probe(directory)
            self.assertTrue((Path(directory) / 'ik_probe.json').is_file())
            self.assertFalse((Path(directory) / 'trials.jsonl').exists())
        self.assertEqual(response.identity, request.identities[0])
        self.assertEqual(response.identity.trial, -1)
        self.assertEqual(response.plan_count, 5)
        self.assertEqual(request.budgets[0].plan_count, 5)
        self.assertTrue(response.plan['probe']['diagnostic_only'])
        self.assertEqual(response.plan['probe']['queue_s'], .5)
        options = service.planners.acquire.call_args.args[1]
        self.assertEqual(options.local_base_radius_m, .35)
        self.assertEqual(options.operation_base_mode, 'holonomic_joint_planning')
        self.assertEqual(options.num_ik_seeds, 32)
        self.assertEqual(service.planners.acquire.call_args.kwargs['batch_size'], 16)
        self.assertTrue(service.planners.acquire.call_args.kwargs['ik_only'])
        self.assertEqual(len(planner.diagnostic_ik.call_args.args[0]), 16)
        self.assertEqual(request.inputs['candidates'], [{'row': index} for index in range(17)])

    def test_expired_budget_does_not_start_an_ik_solve(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            response, request, planner, service = self.execute_probe(directory, limit=0.)
        planner.diagnostic_ik.assert_not_called()
        self.assertEqual(response.plan['probe']['status'], 'unresolved')
        self.assertEqual(response.plan['probe']['reason'], 'probe_active_budget')
        self.assertIsNone(response.plan['probe']['feasible_ratio'])
