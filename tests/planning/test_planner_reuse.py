"""Station resource ownership, new-trial seeds and simulation rebinding."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.planning.curobo_v2 import StationPlannerCache, V2Planner, reset_motion_seed


class PlannerReuseTests(unittest.TestCase):
    def test_missing_cuda_is_shared_failure_before_loading_native_solvers(self):
        from lastmile_dataflow.runtime.execution.lifecycle import classify_episode_exception
        with patch('torch.cuda.is_available', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'V2 requires CUDA') as caught:
                V2Planner(None, None, None, None, workspace=None)
        self.assertEqual(classify_episode_exception(caught.exception)['failure_scope'], 'shared')

    def test_only_identical_unstarted_station_reuses_preclose_planners(self):
        model = object()
        def sim(value=0., started=False):
            return SimpleNamespace(model=model, started=started, state_vector=lambda: np.array([value]))

        def create(simulation, config, side, path, *, workspace):
            planner = Mock()
            planner.initial_model, planner.initial_state = simulation.model, simulation.state_vector()
            planner.matches_initial = lambda candidate: V2Planner.matches_initial(planner, candidate)
            return planner

        cache = StationPlannerCache()
        with patch('lastmile_dataflow.planning.curobo_v2.V2Planner', side_effect=create) as factory:
            first = cache.acquire(sim(), SimpleNamespace(seed=17), 'left', Path('one'), workspace=None)
            cache.release(first)
            second = cache.acquire(sim(), SimpleNamespace(seed=18), 'left', Path('two'), workspace=None)
            self.assertIs(first, second)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(second.bind_trial.call_args.args[1].seed, 18)
            closed = cache.acquire(sim(started=True), None, 'left', Path('closed'), workspace=None)
            self.assertIsNot(closed, first)
            cache.release(closed)
            closed.close.assert_called_once()
            changed = cache.acquire(sim(1.), None, 'left', Path('changed'), workspace=None)
            self.assertIsNot(changed, first)
            first.close.assert_called_once()
            cache.close()
            changed.close.assert_called_once()

    def test_new_seed_recreates_managers_and_resets_all_native_streams(self):
        class Manager:
            def __init__(self, device, dim, low, high, *, random_seed, action_horizon):
                self.seed = random_seed
                self.rng = np.random.default_rng(random_seed)

        cores = [SimpleNamespace(
            config=SimpleNamespace(device_cfg='cpu', random_seed=17), action_dim=3,
            auxiliary_rollout=SimpleNamespace(action_bound_lows=-1, action_bound_highs=1, action_horizon=h),
            seed_manager=Manager('cpu', 3, -1, 1, random_seed=17, action_horizon=h)) for h in (1, 20)]
        motion = SimpleNamespace(ik_solver=SimpleNamespace(core=cores[0]),
                                 trajopt_solver=SimpleNamespace(core=cores[1]), reset_seed=Mock())
        previous = [core.seed_manager for core in cores]
        reset_motion_seed(motion, 18)
        for old, core in zip(previous, cores):
            self.assertIsNot(old, core.seed_manager)
            self.assertEqual(core.config.random_seed, 18)
            np.testing.assert_array_equal(core.seed_manager.rng.random(5), np.random.default_rng(18).random(5))
        motion.reset_seed.assert_called_once()

    def test_bind_updates_model_fov_callback_and_log_ownership(self):
        from lastmile_dataflow.planning import curobo_v2
        planner = V2Planner.__new__(V2Planner)
        planner.detach = Mock()
        planner.model = SimpleNamespace(sim=object())
        planner.context = {'contact_allowed': True}
        planner.base = np.eye(4)
        planner.names = ('base_x',)
        planner.side = 'left'
        planner.query_log, planner.solver_log, planner.fov_log = [1], [2], [3]
        planner.fov_definition = {'camera_id': 0}
        planner.args = SimpleNamespace(to_device=lambda value: value)
        planner.motion = SimpleNamespace(tool_frames=['tcp'], update_tool_pose_criteria=Mock())
        simulation = SimpleNamespace(data=SimpleNamespace(xpos=np.array([[1., 2., 3.]])), target_id=0)
        config = SimpleNamespace(head_fov_enabled=True, head_fov_min_depth_m=.01, seed=18)
        criteria_module = SimpleNamespace(ToolPoseCriteria=object)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            planner.resource_path = root / 'rollouts/r0/open'
            target = root / 'rollouts/r1/open'
            target.mkdir(parents=True)
            with (patch.dict('sys.modules', {'curobo._src.cost.tool_pose_criteria': criteria_module}),
                  patch.object(curobo_v2, 'SparseFOVValidator') as validator,
                  patch.object(curobo_v2, 'reset_motion_seed') as reset):
                planner.bind_trial(simulation, config, target)
            self.assertIs(planner.sim, simulation)
            self.assertIs(planner.model.sim, simulation)
            self.assertIs(validator.call_args.args[0], simulation)
            self.assertIs(validator.call_args.args[2].__self__, planner)
            reset.assert_called_once_with(planner.motion, 18)
            self.assertEqual(planner.query_log + planner.solver_log + planner.fov_log, [])
            self.assertFalse(planner.context['contact_allowed'])
            self.assertIsNone(planner.on_query)
            self.assertTrue((target / 'planner_reuse.json').exists())
