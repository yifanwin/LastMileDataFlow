"""Station resource ownership, new-trial seeds and simulation rebinding."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.planning.curobo.planner import (
    V2Planner,
    reset_motion_seed,
    _compute_target_bodies,
    _compute_handle_bodies,
)
from lastmile_dataflow.planning.curobo.context import PlannerContext


class PlannerReuseTests(unittest.TestCase):
    def test_missing_cuda_is_shared_failure_before_loading_native_solvers(self):
        from lastmile_dataflow.runtime.execution.lifecycle import classify_episode_exception

        with patch("torch.cuda.is_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "V2 requires CUDA") as caught:
                V2Planner(None, None, None, None, workspace=None)
        self.assertEqual(classify_episode_exception(caught.exception)["failure_scope"], "shared")

    def test_new_seed_recreates_managers_and_resets_all_native_streams(self):
        class Manager:
            def __init__(self, device, dim, low, high, *, random_seed, action_horizon):
                self.seed = random_seed
                self.rng = np.random.default_rng(random_seed)

        cores = [
            SimpleNamespace(
                config=SimpleNamespace(device_cfg="cpu", random_seed=17),
                action_dim=3,
                auxiliary_rollout=SimpleNamespace(action_bound_lows=-1, action_bound_highs=1, action_horizon=h),
                seed_manager=Manager("cpu", 3, -1, 1, random_seed=17, action_horizon=h),
            )
            for h in (1, 20)
        ]
        motion = SimpleNamespace(
            ik_solver=SimpleNamespace(core=cores[0]), trajopt_solver=SimpleNamespace(core=cores[1]), reset_seed=Mock()
        )
        previous = [core.seed_manager for core in cores]
        reset_motion_seed(motion, 18)
        for old, core in zip(previous, cores):
            self.assertIsNot(old, core.seed_manager)
            self.assertEqual(core.config.random_seed, 18)
            np.testing.assert_array_equal(core.seed_manager.rng.random(5), np.random.default_rng(18).random(5))
        motion.reset_seed.assert_called_once()

    def test_target_binding_uses_current_simulation_not_first_task(self):
        """Different models or same model with different target recompute body sets."""
        from mujoco import MjModel

        xml_a = """<mujoco><worldbody><body name="root"><geom type="sphere" size=".1"/>
                   <body name="target_a"><geom type="box" size=".05 .05 .05"/>
                   <body name="leaf_a"><geom type="capsule" size=".02" fromto="0 0 0 0 0 .1"/></body></body></body></worldbody></mujoco>"""
        xml_b = """<mujoco><worldbody><body name="root"><geom type="sphere" size=".1"/>
                   <body name="target_b"><geom type="cylinder" size=".03 .06"/>
                   <body name="leaf_b"><geom type="ellipsoid" size=".02 .03 .04"/></body></body></body></worldbody></mujoco>"""
        model_a = MjModel.from_xml_string(xml_a)
        model_b = MjModel.from_xml_string(xml_b)
        target_a_id = model_a.body("target_a").id
        target_b_id_same_value = model_b.body("target_b").id
        leaf_a_id = model_a.body("leaf_a").id
        leaf_b_id = model_b.body("leaf_b").id

        sim_a = SimpleNamespace(model=model_a, target_id=target_a_id, handle_id=None)
        sim_b = SimpleNamespace(model=model_b, target_id=target_b_id_same_value, handle_id=None)

        # First task: model A, target_a + leaf_a
        bodies_a = _compute_target_bodies(sim_a)
        self.assertIn(target_a_id, bodies_a)
        self.assertIn(leaf_a_id, bodies_a)
        self.assertNotIn(model_a.body("root").id, bodies_a)

        # Second task: model B with same numerical target_id but different subtree
        bodies_b = _compute_target_bodies(sim_b)
        self.assertIn(target_b_id_same_value, bodies_b)
        self.assertIn(leaf_b_id, bodies_b)
        # Must not mistakenly use leaf_a_id, which doesn't exist in model_b
        self.assertEqual(bodies_b, {target_b_id_same_value, leaf_b_id})

    def test_handle_bodies_distinguish_pick_open(self):
        """Empty handle_bodies for Pick; non-empty for Open."""
        from mujoco import MjModel

        xml = """<mujoco><worldbody><body name="cabinet"><geom type="box" size=".2 .2 .3"/>
                 <body name="door"><geom type="box" size=".18 .02 .25"/><body name="handle">
                 <geom type="cylinder" size=".015 .05"/></body></body></body></worldbody></mujoco>"""
        model = MjModel.from_xml_string(xml)
        door_id, handle_id = model.body("door").id, model.body("handle").id

        pick = SimpleNamespace(model=model, target_id=door_id, handle_id=None)
        open_task = SimpleNamespace(model=model, target_id=door_id, handle_id=handle_id)

        pick_handles = _compute_handle_bodies(pick)
        self.assertEqual(pick_handles, set())

        open_handles = _compute_handle_bodies(open_task)
        self.assertEqual(open_handles, {handle_id})

    def test_same_model_different_target_updates_carried_fit(self):
        """Planner.fit_matches rejects stale target even if model is the same."""
        from mujoco import MjModel

        xml = """<mujoco><worldbody><body name="table"><geom type="plane" size="1 1 .05"/>
                 <body name="cup"><geom type="cylinder" size=".04 .08"/></body>
                 <body name="bottle"><geom type="capsule" size=".03" fromto="0 0 0 0 0 .15"/></body>
                 </body></worldbody></mujoco>"""
        model = MjModel.from_xml_string(xml)
        cup_id = model.body("cup").id
        bottle_id = model.body("bottle").id

        planner = V2Planner.__new__(V2Planner)
        sim_cup = SimpleNamespace(model=model, target_id=cup_id)
        sim_bottle = SimpleNamespace(model=model, target_id=bottle_id)

        # Initially no fit
        self.assertFalse(planner.fit_matches(sim_cup))

        # After storing cup fit
        planner._fit_model, planner._fit_target = model, cup_id
        self.assertTrue(planner.fit_matches(sim_cup))
        self.assertFalse(planner.fit_matches(sim_bottle))

        # After storing bottle fit
        planner._fit_model, planner._fit_target = model, bottle_id
        self.assertFalse(planner.fit_matches(sim_cup))
        self.assertTrue(planner.fit_matches(sim_bottle))

    def test_pick_attach_empty_target_error_includes_id_and_name(self):
        """Empty target world raises ValueError with target_id and body name before indexing."""
        planner = V2Planner.__new__(V2Planner)
        planner.base = np.eye(4)
        planner.sim = SimpleNamespace(
            target_id=42,
            model=SimpleNamespace(body=Mock(return_value=SimpleNamespace(name="cabinet/door_left_handle"))),
        )
        planner.context = PlannerContext(target_checker=SimpleNamespace(scene_model=SimpleNamespace(mesh=[])))

        with patch("lastmile_dataflow.planning.curobo.planner.body_pose", return_value=np.eye(4)):
            with self.assertRaisesRegex(ValueError, r"empty target world.*target_id=42.*cabinet/door_left_handle"):
                planner.attach_target_geometry()
