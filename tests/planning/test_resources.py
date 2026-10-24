"""Bounded solver ownership and propagation of measured limits/locks; no GPU solve."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch

from lastmile_dataflow.planning.curobo_v2 import PlannerResources, V2Planner


class ResourceTests(unittest.TestCase):
    def test_solver_families_are_reused_and_bounded_without_retaining_trial_views(self):
        resources = PlannerResources()
        sim = SimpleNamespace(robot=SimpleNamespace(config=SimpleNamespace(model_path='panda', control_profile='position-v1')))
        config = SimpleNamespace(operation_base_mode='holonomic_joint_planning', num_ik_seeds=32,
                                 num_trajopt_seeds=4, max_candidates=16, head_fov_enabled=False)
        planners = []

        def create(*args, **kwargs):
            planner = Mock()
            planner.model = SimpleNamespace(sim=sim)
            planners.append(planner)
            return planner

        with patch('lastmile_dataflow.planning.curobo_v2.V2Planner', side_effect=create) as factory:
            for name in ('first', 'second'):
                with resources.acquire(sim, config, 'right', Path(name), workspace=None, batch_size=3):
                    pass
            self.assertEqual(factory.call_count, 1)
            planners[0].rebind_resources.assert_called_once_with(sim, config, Path('second'), None)
            self.assertIsNone(planners[0].model.sim)
            config.operation_base_mode = 'fixed'
            with resources.acquire(sim, config, 'right', Path('closed'), workspace=None, batch_size=1):
                pass
            sim.robot.config.model_path = 'another-model'
            with resources.acquire(sim, config, 'right', Path('another'), workspace=None, batch_size=1):
                pass
        self.assertEqual(len(resources.planners), 2)
        planners[0].close.assert_called_once()
        resources.close()
        self.assertTrue(all(planner.close.call_count == 1 for planner in planners))

    def test_rebind_updates_solver_lm_and_graph_kinematics_and_sampling_bounds(self):
        from curobo.types import DeviceCfg

        def kinematics():
            return SimpleNamespace(update_kinematics_config=Mock())

        models = [kinematics() for _ in range(7)]
        rollout = lambda model: SimpleNamespace(transition_model=SimpleNamespace(robot_model=model))
        low, high = torch.full((2,), -.4), torch.full((2,), .4)
        lm = SimpleNamespace(_robot_model=models[4], _aux_robot_model=models[5],
            config=SimpleNamespace(joint_limit_margin=.05, max_step_size=.1),
            joint_limits=SimpleNamespace(position=torch.stack((low, high))),
            action_min=torch.full((2,), -9.), action_max=torch.full((2,), 9.),
            action_step_max=torch.full((2,), 9.))
        sampler = SimpleNamespace(range_b=torch.ones(2), low_bounds=torch.zeros(2), up_bounds=torch.ones(2))
        motion = SimpleNamespace(
            ik_solver=SimpleNamespace(core=SimpleNamespace(get_all_rollout_instances=lambda: [rollout(models[0])]), seed_ik_solver=lm),
            trajopt_solver=SimpleNamespace(core=SimpleNamespace(get_all_rollout_instances=lambda: [rollout(models[1])])),
            kinematics=models[2], graph_planner=SimpleNamespace(
                get_all_rollout_instances=lambda: [rollout(models[3]), rollout(models[6])],
                action_bound_lows=low, action_bound_highs=high,
                sampling_strategy=SimpleNamespace(action_sample_generator=sampler)))
        planner = V2Planner.__new__(V2Planner)
        planner.motion, planner.args, planner.side = motion, DeviceCfg(device='cpu'), 'right'
        planner.detach, planner.refresh_world, planner.check_fk = Mock(), Mock(), Mock()
        planner.context, planner.query_log, planner.solver_log, planner.fov_log = {}, [], [], []
        model = SimpleNamespace(names=('a', 'b'), base=np.eye(4), reference_yaw=0., h_step_limit=0.,
                                cfg={'kinematics': {}}, movable_base=True)
        sim = SimpleNamespace(robot=SimpleNamespace(planning_model=Mock(return_value=model)),
                              model=object(), state_vector=lambda: np.zeros(3))
        parameters = object()
        with tempfile.TemporaryDirectory() as directory:
            planner.resource_path = Path(directory) / 'first'
            with (patch('curobo._src.types.robot.RobotCfg.create',
                        return_value=SimpleNamespace(kinematics=SimpleNamespace(kinematics_config=parameters))),
                  patch('lastmile_dataflow.planning.curobo_v2.reset_motion_seed')):
                planner.rebind_resources(sim, SimpleNamespace(seed=42), Path(directory), ([1., 0., .5], 1.))
        for native in models:
            native.update_kinematics_config.assert_called_once_with(parameters)
        torch.testing.assert_close(lm.action_min, torch.full((2,), -.36))
        torch.testing.assert_close(lm.action_max, torch.full((2,), .36))
        torch.testing.assert_close(lm.action_step_max, torch.full((2,), .072))
        torch.testing.assert_close(sampler.range_b, torch.full((2,), .8))
        torch.testing.assert_close(sampler.low_bounds, low)
        planner.check_fk.assert_called_once()
