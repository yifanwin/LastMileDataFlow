"""Offline fault regressions; no real planner, rendering or GPU workers."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.runtime.no_edit_execution import OperationFailure
from lastmile_dataflow.runtime.no_edit_v2 import manipulate_v2
from lastmile_dataflow.stations.no_edit_config import NoEditConfig


@dataclass
class FakeRobotConfig:
    name: str = 'offline-fixture'


class RetryDirectoryTests(unittest.TestCase):
    def context(self, path):
        return SimpleNamespace(
            recorder=SimpleNamespace(path=path), config=NoEditConfig(),
            sim=SimpleNamespace(model=Mock(), robot=Mock()),
            plan_count=0, plan_serial=0, max_plans=10, plans=[], deadline=Mock(),
            begin=Mock(), follow_points=Mock(), follow=Mock(), arm_tick=Mock(),
            hold_torso_target=Mock(return_value=0.))

    def test_attempt_retry_never_overwrites_earlier_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(Path(d)/'attempt')
            planner=Mock()
            def grasp(goals, query, **kwargs):
                query('goalset')
                return (False, 0, {}, {'reason':'no_solution'})
            planner.grasp.side_effect=grasp
            candidate={'body':'target','pose_local':np.eye(4),'row':0,'source':'fixture'}
            with patch('lastmile_dataflow.runtime.no_edit_v2.candidate_pool',return_value=[candidate]), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.body_pose',return_value=np.eye(4)), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.V2Planner',return_value=planner):
                for _ in range(2):
                    ctx.plan_count=0
                    with self.assertRaisesRegex(OperationFailure,'planning_no_solution'):
                        manipulate_v2(ctx,{'operation':'pick','anchor_world':[0,0,0]},[],d,0,
                                      forced={'arm':'left'})
            self.assertTrue((ctx.recorder.path/'v080-0-left').is_dir())
            self.assertTrue((ctx.recorder.path/'v080-1-left').is_dir())
            self.assertEqual(planner.grasp.call_count,2)

    def test_closed_directory_can_be_reused(self):
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(Path(d))
            ctx.plan_serial=1
            closed=Path(d)/'v080-closed-1-left'; closed.mkdir()
            marker=closed/'previous.json'; marker.write_text('{}')
            planner=Mock()
            planner.grasp.return_value=(True,0,{'pregrasp':[],'approach':[]},{})
            candidate={'body':'target','pose_local':np.eye(4),'row':0,'source':'fixture'}
            ctx.sim.robot.group.return_value=np.zeros(7)
            monitor=SimpleNamespace(fingers=[], initial={})
            with patch('lastmile_dataflow.runtime.no_edit_v2.candidate_pool',return_value=[candidate]), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.body_pose',return_value=np.eye(4)), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.PickMonitor',return_value=monitor), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.V2Planner',side_effect=[planner,RuntimeError('reached closed planner')]) as factory:
                with self.assertRaisesRegex(RuntimeError,'reached closed planner'):
                    manipulate_v2(ctx,{'operation':'pick','anchor_world':[0,0,0]},[],d,0,
                                  forced={'arm':'left'})
            self.assertEqual(factory.call_args.args[3],closed)
            self.assertTrue(marker.exists())

    def test_open_world_directory_can_be_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'attempt'
            ctx=self.context(path)
            ctx.plan_serial=1
            ctx.open_joint=0
            ctx.arm_tick=Mock(side_effect=lambda *a,**k: ctx.samples.append({'finger_forces_n':[0.,0.]}))
            ctx.sim=SimpleNamespace(model=Mock(), robot=SimpleNamespace(
                config=SimpleNamespace(control_hz=20), group=Mock(return_value=np.zeros(7))),
                data=SimpleNamespace(qpos=np.zeros(1), xaxis=np.zeros((1,3)), xanchor=np.zeros(3)))
            ctx.sim.model.jnt_qposadr=np.zeros(1, dtype=int)
            ctx.sim.model.joint=Mock(return_value=SimpleNamespace(id=0))
            closed=path/'v080-closed-1-left'
            world=closed/'world-0'; world.mkdir(parents=True)
            marker=world/'previous.json'; marker.write_text('{}')
            planner=Mock()
            planner.grasp.return_value=(True,0,{'pregrasp':[],'approach':[]},{})
            candidate={'body':'target','pose_local':np.eye(4),'row':0,'source':'fixture'}
            monitor=SimpleNamespace(fingers=[], initial={})
            task={'operation':'open','anchor_world':[0,0,0],'joint_name':'door',
                  'joint_kind':'slide','joint_initial':0.,'joint_goal':.1}
            with patch('lastmile_dataflow.runtime.no_edit_v2.candidate_pool',return_value=[candidate]), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.body_pose',return_value=np.eye(4)), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.PickMonitor',return_value=monitor), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.tcp_pose',return_value=np.eye(4)), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.evaluate_open',return_value={'status':'success','reason':'open'}), \
                 patch('lastmile_dataflow.runtime.no_edit_v2.V2Planner',side_effect=[planner,Mock()]):
                verdict,control=manipulate_v2(ctx,task,[],d,0,forced={'arm':'left'})
            self.assertEqual(verdict['status'],'success')
            self.assertTrue(marker.exists())
