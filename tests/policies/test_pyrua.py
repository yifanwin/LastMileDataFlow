"""PyRUA backend boundaries; real primitives, RLDX-1 and the agent live in smoke/pyrua.py."""
import threading
import unittest
from pathlib import Path

import numpy as np

from lastmile_dataflow.config import PandaOmronConfig, RobotConfig
from lastmile_dataflow.policies.config import check_profile, load_policy_config
from lastmile_dataflow.runtime.execution.agent import OwnerThread, native_base_xy, native_to_project, world_map
from lastmile_dataflow.runtime.execution_config import load_execution_config
from lastmile_dataflow.runtime.outcomes.vla import TaskEvidence, evaluate
from tests.helpers import evidence

POLICY = 'configs/policies/pyrua_panda_omron.json'


class PyruaConfigTests(unittest.TestCase):
    def test_explicit_config_and_profile(self):
        config = load_policy_config(POLICY)
        self.assertEqual((config.embodiment, config.runtime, config.arm), ('panda_omron', 'codex', 'cells'))
        self.assertEqual(config.task_horizon, config.control_step_budget)
        self.assertTrue(config.vla_model_path.endswith('data/checkpoints/rldx-1-ft-rc365'))
        self.assertEqual(len(config.vla_site_packages), 2)
        execution = load_execution_config('configs/execution/pyrua_panda_omron.json')
        self.assertEqual((execution.operation_backend, execution.operation_base_mode), ('pyrua', 'policy_relative'))
        check_profile(PandaOmronConfig('unused', control_profile='robocasa-v1'), 'pyrua', POLICY)
        with self.assertRaisesRegex(ValueError, 'embodiment'):
            check_profile(RobotConfig('unused'), 'pyrua', POLICY)
        with self.assertRaisesRegex(ValueError, 'control profile'):
            check_profile(PandaOmronConfig('unused'), 'pyrua', POLICY)


class NativeActionTests(unittest.TestCase):
    def test_gripper_sign_and_base_mode_follow_robosuite(self):
        action = np.zeros(12)
        action[6], action[11] = .3, -1.   # a small positive servo value still closes
        result, grip = native_to_project(action, 0.)
        self.assertEqual((result[6], result[11], grip), (1., 0., 1.))
        action[6], action[11] = 0., .2    # zero keeps the previous command
        result, grip = native_to_project(action, grip)
        self.assertEqual((result[6], result[11]), (1., 1.))
        action[6] = -.01
        self.assertEqual(native_to_project(action, grip)[0][6], 0.)
        with self.assertRaises(ValueError):
            native_to_project(np.zeros(11), 0.)

    def test_base_command_is_identity_when_frames_coincide(self):
        deadband = np.array([.25, .25])
        for command in ([.5, 0.], [-.7, .4], [.1, 0.]):
            expected = np.array(command) * (np.abs(command) > .25)
            np.testing.assert_allclose(native_base_xy(np.array(command), 0., 0., deadband), expected, atol=1e-12)

    def test_rotated_station_reproduces_robosuite_steady_velocity(self):
        deadband = np.array([.25, .25])

        def rotation(angle):
            return np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])

        def clipped(velocity):
            return np.sign(velocity)*np.maximum(np.abs(velocity)-deadband, 0.)

        for initial, yaw, native in ((.6, .6, [.8, 0.]), (.6, 1.1, [.8, .3]), (-2., -1.5, [-.5, .9])):
            command = native_base_xy(np.array(native), yaw, initial, deadband)
            # Project: the adapter rotates by the yaw change into world-aligned joints.
            project = clipped(rotation(yaw-initial) @ command)
            # robosuite: joints aligned with the episode's initial heading.
            robosuite = rotation(initial) @ clipped(rotation(yaw-initial) @ np.array(native))
            np.testing.assert_allclose(project, robosuite, atol=1e-12)
        forward = clipped(native_base_xy(np.array([.8, 0.]), .6, .6, deadband))
        self.assertAlmostEqual(np.arctan2(forward[1], forward[0]), .6, places=12)


class WorldMapTests(unittest.TestCase):
    def test_pixels_back_project_to_rendered_points(self):
        transform = np.eye(4)
        transform[:3, :3] = [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]   # optical z along world +x
        transform[:3, 3] = [1., 2., 1.5]
        calibration = {'intrinsics': {'K': [[100, 0, 31.5], [0, 100, 23.5], [0, 0, 1]]},
                       'T_world_camera_optical': transform.tolist()}
        depth = np.full((48, 64), 2.)
        depth[0, 0], depth[1, 1] = 0., 50.
        world = world_map(depth, calibration, far_m=50.)
        np.testing.assert_allclose(world[23, 31], [3., 2.01, 1.51])
        self.assertTrue(np.isnan(world[0, 0]).all() and np.isnan(world[1, 1]).all())
        self.assertGreater(world[10, 31, 2], world[40, 31, 2])   # row 0 is the image top


class OwnerThreadTests(unittest.TestCase):
    def test_calls_from_workers_run_on_owner(self):
        owner = OwnerThread()
        seen = []

        def work():
            seen.append(owner.call(threading.get_ident))
            with self.assertRaisesRegex(KeyError, 'boom'):
                owner.call(lambda: {}['boom'])

        worker = threading.Thread(target=work)
        worker.start()
        owner.serve(worker)
        self.assertEqual(seen, [threading.get_ident()])


class StreamingEvidenceTests(unittest.TestCase):
    def test_incremental_verdicts_equal_fresh_evaluation(self):
        task = {'operation': 'pick'}
        for conditions in ({}, {'support': True}, {'gap': True}, {'slip': True}):
            initial, samples = evidence(2.2, **conditions)
            stream = TaskEvidence(task, {'right': initial}, 1.)
            for index, row in enumerate(samples):
                stream.add('right', row)
                if index % 97 == 0 or index == len(samples)-1:
                    self.assertEqual(stream.verdict(), evaluate(task, {'right': samples[:index+1]}, {'right': initial}, 1.))


@unittest.skipUnless(Path(load_policy_config(POLICY).rpent_root, 'robots').is_dir(), 'RPent checkout not prepared')
class HostTests(unittest.TestCase):
    def setUp(self):
        load_policy_config(POLICY).import_paths()

    def test_prompt_edits_apply_once_for_both_primitive_sets(self):
        from pyrualean.prompt import api_reference
        from lastmile_dataflow.policies.pyrua import host
        for primitives in ('full', 'no-vla'):
            reference = api_reference(host.LastMilePandaRobot, primitives=primitives)
            knowledge = host.lastmile_knowledge(primitives=primitives)
            self.assertIn('LastMileDataFlow house scene', reference)
            self.assertNotIn('kitchen', reference + knowledge)
            self.assertIn("project's physical predicate", knowledge)

    def test_rldx_action_reassembles_in_composite_order(self):
        from lastmile_dataflow.policies.pyrua.host import ProjectEnvClient, unmap_action
        raw = {'action.end_effector_position': np.array([.1, .2, .3]),
               'action.end_effector_rotation': np.array([.4, .5, .6]),
               'action.gripper_close': np.array([.7]), 'action.base_motion': np.array([.01, .02, .03, .04]),
               'action.control_mode': np.array([.2])}
        flat = ProjectEnvClient.reassemble_env_action(None, unmap_action(raw))
        np.testing.assert_allclose(flat, [.1, .2, .3, .4, .5, .6, 1., .01, .02, .03, .04, -1.])
        source = Path(load_policy_config(POLICY).rpent_root).parents[0]/'robocasa/robocasa/wrappers/gym_wrapper.py'
        if source.is_file():
            text = source.read_text()
            for line in ('"robot0_torso": input_action["action.base_motion"][..., 3:4],',
                         '-1.0 if input_action["action.control_mode"] < 0.5 else 1.0'):
                self.assertIn(line, text)
