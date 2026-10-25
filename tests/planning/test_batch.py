"""Trial-seeded native CPU samples and numerical batch result ownership."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch

from lastmile_dataflow.planning.curobo_v2 import V2Planner, bind_batch_seeds


class BatchTests(unittest.TestCase):
    def test_native_seed_samples_stay_with_trial_when_batch_order_changes(self):
        from curobo.types import DeviceCfg
        from curobo._src.solver.manager_seed import SeedManager

        device = DeviceCfg(device='cpu')
        rollout = SimpleNamespace(action_bound_lows=torch.full((3,), -1.),
                                  action_bound_highs=torch.ones(3), action_horizon=1)
        def core():
            return SimpleNamespace(config=SimpleNamespace(device_cfg=device), action_dim=3,
                auxiliary_rollout=rollout, seed_manager=SeedManager(device, 3,
                    rollout.action_bound_lows, rollout.action_bound_highs))
        lm = SimpleNamespace(dof=3, device_cfg=device, action_min=rollout.action_bound_lows,
                             action_max=rollout.action_bound_highs, config=SimpleNamespace(num_seeds=8))
        motion = SimpleNamespace(ik_solver=SimpleNamespace(core=core(), seed_ik_solver=lm),
                                 trajopt_solver=SimpleNamespace(core=core()))
        def samples(seeds):
            bind_batch_seeds(motion, seeds)
            manager = motion.ik_solver.core.seed_manager
            initial = torch.zeros((3, 1, 3))
            return (manager.generate_random_actions(3, 32).numpy(),
                    manager.generate_random_actions(3, 32).numpy(),
                    lm._generate_seed_configs(3, initial).numpy())
        first, next_first, first_lm = samples([42, 43, 44])
        reordered, next_reordered, reordered_lm = samples([44, 42, 43])
        for original, new in enumerate((1, 2, 0)):
            np.testing.assert_array_equal(first[original], reordered[new])
            np.testing.assert_array_equal(next_first[original], next_reordered[new])
            np.testing.assert_array_equal(first_lm[original], reordered_lm[new])
        self.assertFalse(np.array_equal(first[0], first[1]))
        self.assertFalse(np.array_equal(first_lm[0], first_lm[1]))
        self.assertEqual(first_lm.shape, (3, 8, 3))
        np.testing.assert_array_equal(first_lm[:, 0], 0.)

    def test_failed_batch_slot_has_no_executable_trajectory_and_keeps_its_seed(self):
        planner = V2Planner.__new__(V2Planner)
        result = SimpleNamespace(success=torch.tensor([True, False, True]),
            goalset_index=torch.tensor([[0], [1], [2]]), status='fixture',
            approach_interpolated_trajectory=np.arange(18).reshape(3, 3, 2),
            grasp_interpolated_trajectory=np.arange(18, 36).reshape(3, 3, 2),
            approach_interpolated_last_tstep=np.array([2, 3, 1]),
            grasp_interpolated_last_tstep=np.array([1, 3, 2]))
        planner.motion = SimpleNamespace(plan_grasp=Mock(return_value=result),
            tool_frames=['tcp'], joint_names=['a', 'b'], update_tool_pose_criteria=Mock())
        from curobo.types import DeviceCfg
        planner.args = DeviceCfg(device='cpu')
        planner.current_joints = lambda: np.zeros(2)
        planner.points = lambda trajectory, last: trajectory[0, :int(last[0])].tolist()
        planner.detach = Mock()
        planner.config = SimpleNamespace(max_attempts=5, time_dilation=.5)
        planner.names, planner.base = ('a', 'b'), np.eye(4)
        planner.sim = SimpleNamespace(robot=SimpleNamespace(config=SimpleNamespace(control_hz=20)))
        planner.query_log, planner.solver_log, planner.context = [], [], {}
        with tempfile.TemporaryDirectory() as directory:
            planner.path = Path(directory)
            with (patch('lastmile_dataflow.planning.curobo_v2.bind_batch_seeds'),
                  patch.dict('sys.modules', {'curobo._src.cost.tool_pose_criteria':
                      SimpleNamespace(ToolPoseCriteria=object)})):
                goals = [np.eye(4) for _ in range(3)]
                for index, goal in enumerate(goals):
                    goal[0, 3] = index
                lanes = planner.grasp_batch([[goal] for goal in goals], [42, 43, 44], Mock())
        self.assertEqual([lane['diagnostics']['seed'] for lane in lanes], [42, 43, 44])
        self.assertEqual(lanes[1]['stages'], {})
        self.assertFalse(lanes[1]['success'])
        self.assertEqual(lanes[0]['stages']['pregrasp'], result.approach_interpolated_trajectory[0, :2].tolist())
        self.assertEqual(lanes[2]['stages']['approach'], result.grasp_interpolated_trajectory[2, :2].tolist())
        self.assertFalse(planner.motion.plan_grasp.call_args.kwargs['plan_grasp_to_lift'])
        native_goals, native_state = planner.motion.plan_grasp.call_args.args
        np.testing.assert_array_equal(native_goals.position[:, 0, 0, 0, 0].numpy(), [0, 1, 2])
        self.assertEqual(native_state.position.shape, (3, 2))
        self.assertEqual(lanes[0]['diagnostics']['planning_s'], lanes[2]['diagnostics']['planning_s'])
