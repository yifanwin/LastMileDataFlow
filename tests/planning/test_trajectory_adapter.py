"""CPU interface tests; these do not claim a GPU optimization passed."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch

from lastmile_dataflow.config import BaseMapConfig
from lastmile_dataflow.planning.curobo.context import PlannerContext
from lastmile_dataflow.planning.curobo.planner import V2Planner
from lastmile_dataflow.planning.trajectory_quality import TrajectoryCandidate


class AdapterTests(unittest.TestCase):
    def test_each_initial_value_has_its_lane_and_pose_goal_does_not_lock_q_seed(self):
        from curobo.types import DeviceCfg
        from curobo._src.types.control_space import ControlSpace
        planner = V2Planner.__new__(V2Planner)
        planner.batch_size, planner.base = 8, np.eye(4)
        planner.names = [f'j{i}' for i in range(7)]
        planner.args = DeviceCfg(device='cpu')
        planner.config = SimpleNamespace(trajectory_quality=BaseMapConfig())
        planner.model = SimpleNamespace(tcp_link='tcp')
        planner.context = PlannerContext(target_checker=None)
        solver = Mock()
        solver.action_horizon = 16
        solver.auxiliary_rollout.transition_model.control_space = ControlSpace.BSPLINE_3
        t = torch.linspace(0, 8, 65)
        velocity = torch.arange(1., 9.)[:, None, None].expand(8, 65, 7)*.01
        native = SimpleNamespace(position=velocity*t[None, :, None], velocity=velocity,
                                 acceleration=torch.zeros_like(velocity), dt=torch.full((8,), .125))
        native.reorder = lambda names: native
        solver.solve_pose.return_value = SimpleNamespace(interpolated_trajectory=native,
            success=torch.ones((8, 1), dtype=torch.bool), interpolated_last_tstep=torch.full((8, 1), 65),
            js_solution=SimpleNamespace(knot=torch.zeros((8, 1, 4, 7))))
        planner.motion = SimpleNamespace(joint_names=planner.names, trajopt_solver=solver)
        planner.batch_goals, planner.state = Mock(), Mock()
        candidates = [TrajectoryCandidate(i, i//2, np.zeros(7), 'linear') for i in range(6)]
        with patch('torch.cuda.synchronize'):
            goals = np.tile(np.eye(4), (6, 1, 1))
            goals[:, 0, 3] = np.arange(6)
            actual, _ = planner.trajectory_candidates(goals, candidates, np.zeros((6, 16, 7)))
        self.assertEqual(len(actual), 6)  # padding is not a logical attempt
        call = solver.solve_pose.call_args.kwargs
        self.assertFalse(call['use_implicit_goal'])
        self.assertIsNone(call['goal_state'])
        self.assertEqual((call['num_seeds'], call['return_seeds']), (1, 1))
        self.assertEqual(tuple(call['seed_traj'].shape), (8, 1, 16, 7))
        self.assertIsNone(planner.context.trajectory_goals)
        passed_goals = planner.batch_goals.call_args.args[0]
        np.testing.assert_equal([g[0][0, 3] for g in passed_goals], [0, 1, 2, 3, 4, 5, 0, 0])
        np.testing.assert_allclose(actual[5].curve.at(8), .48, atol=1e-6)

    def test_approach_soft_cost_has_gradient_and_is_not_an_endpoint_lock(self):
        from lastmile_dataflow.planning.curobo.costs import ContactManager
        manager = ContactManager.__new__(ContactManager)
        cfg = SimpleNamespace(**vars(BaseMapConfig()), tcp_link='tcp', spline_intervals=20)
        manager.context = PlannerContext(target_checker=None, trajectory_config=cfg,
            trajectory_goals=torch.tensor([[0., 0., 0., 1., 0., 0., 0.]]))
        position = torch.zeros((1, 11, 1, 3), requires_grad=True)
        quaternion = torch.tensor([1., 0., 0., 0.]).expand(1, 11, 1, 4)
        state = SimpleNamespace(cuda_robot_model_state=SimpleNamespace(tool_poses=SimpleNamespace(
            tool_frames=['tcp'], position=position, quaternion=quaternion)))
        cost = manager._approach_cost(state)
        cost.sum().backward()
        self.assertTrue(torch.equal(cost[:, :8], torch.zeros_like(cost[:, :8])))
        self.assertGreater(float(position.grad[0, 8, 0, 2]), 0.)  # descent moves backward to nominal pregrasp
        self.assertEqual(float(position.grad[0, 10, 0, 2]), 0.)
        # A stationary native terminal padding interval must have zero depth target.
        dense_position = torch.zeros((1, 21, 1, 3), requires_grad=True)
        state.cuda_robot_model_state.tool_poses.position = dense_position
        state.cuda_robot_model_state.tool_poses.quaternion = quaternion[:, :1].expand(1, 21, 1, 4)
        manager._approach_cost(state).sum().backward()
        self.assertEqual(float(dense_position.grad[0, 19, 0, 2]), 0.)


    def test_soft_goals_follow_native_line_search_lane_indices(self):
        from lastmile_dataflow.planning.curobo.costs import ContactManager
        manager = ContactManager.__new__(ContactManager)
        manager.context = PlannerContext(target_checker=None,
            trajectory_goals=torch.tensor([[1., 0., 0., 1., 0., 0., 0.], [2., 0., 0., 1., 0., 0., 0.]]))
        goal = SimpleNamespace(idxs_link_pose=torch.tensor([[0], [0], [1], [1]]))
        targets = manager._trajectory_targets(goal)
        self.assertEqual(targets[:, 0].tolist(), [1., 1., 2., 2.])


if __name__ == '__main__':
    unittest.main()
