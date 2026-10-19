"""Backend isolation and action chunks; real model checks live in smoke/pi05."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.config import PandaOmronConfig, RobotConfig
from lastmile_dataflow.policies.config import check_profile, load_policy_config
from lastmile_dataflow.policies.molmobot import MolmoBotClient
from lastmile_dataflow.policies.pi05.client import Pi05Client
from lastmile_dataflow.runtime.control.panda_robocasa import rotation_matrix, rotation_vector
from lastmile_dataflow.runtime.execution_config import load_execution_config


class Pi05Tests(unittest.TestCase):
    def test_explicit_configs_and_control_profiles(self):
        panda = load_policy_config('configs/policies/pi05_panda_omron.json')
        self.assertEqual((panda.state_size,panda.action_size),(16,12))
        self.assertTrue(panda.python.endswith('.venv-pi05/bin/python'))
        config=load_execution_config('configs/execution/pi05_panda_omron.json')
        self.assertEqual(config.operation_backend,'pi05')
        robot=PandaOmronConfig('unused',control_profile='robocasa-v1')
        check_profile(robot,'pi05','configs/policies/pi05_panda_omron.json')
        with self.assertRaisesRegex(ValueError,'embodiment'):
            check_profile(RobotConfig('unused'),'pi05','configs/policies/pi05_panda_omron.json')
        with self.assertRaisesRegex(ValueError,'control profile'):
            check_profile(PandaOmronConfig('unused'),'pi05','configs/policies/pi05_panda_omron.json')
        check_profile(robot,'curobo')

    def test_action_buffer_replans_and_does_not_cross_episodes(self):
        client=Pi05Client.__new__(Pi05Client)
        client.config=SimpleNamespace(replan_steps=2)
        client.attempt=None;client.chunk=None;client.index=0;client.serial=0
        replies=[{'actions':np.full((50,12),v), 'raw_actions':np.full((50,32),v),
                  'inference_s':.1,'processed_images':[], 'model_prompt':'task','model_state':np.zeros(32)}
                 for v in (1.,2.,3.)]
        with patch.object(MolmoBotClient,'action',side_effect=replies) as infer:
            result=[client.action({},task={'operation':'pick'},attempt=a,seed=42)
                    for a in ('first','first','first','second')]
        self.assertEqual(infer.call_count,3)
        self.assertEqual([r['action'][0] for r in result],[1.,1.,2.,3.])
        self.assertEqual([r['new_chunk'] for r in result],[True,False,True,True])
        self.assertEqual([r['chunk_index'] for r in result],[0,1,0,0])

    def test_rotation_delta_reconstructs_target_with_left_multiplication(self):
        anchor=rotation_matrix(np.array([.4,-.2,.3]))
        target=rotation_matrix(np.array([-.3,.5,.2]))
        delta=rotation_vector(target@anchor.T)
        np.testing.assert_allclose(rotation_matrix(delta)@anchor,target,atol=1e-12)
