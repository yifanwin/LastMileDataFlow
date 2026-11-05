"""Bounded solver ownership and propagation of measured limits/locks; no GPU solve."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch

from lastmile_dataflow.planning.curobo.planner import PlannerResources, V2Planner
from lastmile_dataflow.planning.curobo.context import PlannerContext


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

        with patch('lastmile_dataflow.planning.curobo.planner.V2Planner', side_effect=create) as factory:
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
        planner.bind_target = Mock()
        planner.context, planner.query_log, planner.solver_log, planner.fov_log = PlannerContext(target_checker=None), [], [], []
        model = SimpleNamespace(names=('a', 'b'), base=np.eye(4), reference_yaw=0., h_step_limit=0.,
                                cfg={'kinematics': {}}, movable_base=True)
        sim = SimpleNamespace(robot=SimpleNamespace(planning_model=Mock(return_value=model)),
                              model=object(), state_vector=lambda: np.zeros(3))
        parameters = object()
        with tempfile.TemporaryDirectory() as directory:
            planner.resource_path = Path(directory) / 'first'
            with (patch('curobo._src.types.robot.RobotCfg.create',
                        return_value=SimpleNamespace(kinematics=SimpleNamespace(kinematics_config=parameters))),
                  patch('lastmile_dataflow.planning.curobo.planner.reset_motion_seed')):
                planner.rebind_resources(sim, SimpleNamespace(seed=42), Path(directory), ([1., 0., .5], 1.))
        for native in models:
            native.update_kinematics_config.assert_called_once_with(parameters)
        torch.testing.assert_close(lm.action_min, torch.full((2,), -.36))
        torch.testing.assert_close(lm.action_max, torch.full((2,), .36))
        torch.testing.assert_close(lm.action_step_max, torch.full((2,), .072))
        torch.testing.assert_close(sampler.range_b, torch.full((2,), .8))
        torch.testing.assert_close(sampler.low_bounds, low)
        planner.check_fk.assert_called_once()
        planner.bind_target.assert_called_once_with(sim)

    def test_pick_and_open_are_separate_families_and_open_rebind_refreshes_handle_world(self):
        resources = PlannerResources()
        robot = SimpleNamespace(config=SimpleNamespace(model_path='panda', control_profile='position-v1'))
        config = SimpleNamespace(operation_base_mode='fixed', num_ik_seeds=32,
                                 num_trajopt_seeds=4, max_candidates=16, head_fov_enabled=False)
        pick = SimpleNamespace(robot=robot, handle_id=None)
        open_a, open_b = SimpleNamespace(robot=robot, handle_id=5), SimpleNamespace(robot=robot, handle_id=9)
        created = []

        def create(sim, *args, **kwargs):
            planner = Mock()
            planner.model = SimpleNamespace(sim=sim)
            planner.has_handle = sim.handle_id is not None
            created.append(planner)
            return planner

        with patch('lastmile_dataflow.planning.curobo.planner.V2Planner', side_effect=create):
            for sim in (pick, open_a, pick, open_b):
                with resources.acquire(sim, config, 'right', Path('p'), workspace=None, batch_size=1):
                    pass
        self.assertEqual([planner.has_handle for planner in created], [False, True])
        created[0].rebind_resources.assert_called_once_with(pick, config, Path('p'), None)
        created[1].rebind_resources.assert_called_once_with(open_b, config, Path('p'), None)

    def test_refresh_world_loads_current_target_and_handle_into_retained_checkers(self):
        from lastmile_dataflow.planning.curobo import planner as planner_module
        planner = V2Planner.__new__(V2Planner)
        target_checker = Mock(data=Mock(get_valid_data=lambda: []))
        handle_checker = Mock(data=Mock(get_valid_data=lambda: []))
        planner.motion = Mock(scene_collision_checker=Mock(data=Mock(get_valid_data=lambda: [])))
        planner.context = PlannerContext(target_checker=target_checker, handle_checker=handle_checker)
        planner.config = SimpleNamespace(head_fov_enabled=False, environment_representation="merged_hull")
        planner.world_range, planner.base, planner.pad_target_query_range = 2., np.eye(4), Mock()
        model = SimpleNamespace(nbody=6, body_parentid=np.array([0, 0, 1, 2, 0, 4]))
        planner.sim = SimpleNamespace(model=model, target_id=1, handle_id=3)
        planner.bind_target(planner.sim)
        exported = []

        def world(sim, path, range_m, *, reference_base, target_only=False, bodies=None, filename=None, cuboids=None, max_cuboids=0):
            exported.append((target_only, None if bodies is None else set(bodies), filename))
            return object()

        with (patch.object(planner_module, 'collision_world', side_effect=world),
              patch('torch.cuda.synchronize')):
            planner.refresh_world(Path('p'))
        self.assertEqual(exported[1:], [(True, {1, 2}, None), (True, {3}, 'handle_world.json')])
        # Same checker objects stay referenced by the existing ContactManager.
        self.assertIs(planner.context.target_checker, target_checker)
        self.assertIs(planner.context.handle_checker, handle_checker)
        target_checker.load_collision_model.assert_called_once()
        handle_checker.load_collision_model.assert_called_once()

        # Open target == handle: target-only world is legitimately empty, handle world is not.
        exported.clear()
        planner.sim = SimpleNamespace(model=model, target_id=4, handle_id=4)
        planner.bind_target(planner.sim)
        with (patch.object(planner_module, 'collision_world', side_effect=world),
              patch('torch.cuda.synchronize')):
            planner.refresh_world(Path('p'))
        self.assertEqual(exported[1:], [(True, set(), None), (True, {4, 5}, 'handle_world.json')])
