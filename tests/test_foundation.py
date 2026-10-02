import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from lastmile_dataflow.config import CollectionConfig, RobotConfig, TaskConfig, construct, freeze_config
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.robots.action import InvalidAction, torso_joints, validate_action
from lastmile_dataflow.integrations.waypoints import PlanResult, arm_waypoint_action, navigation_action
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.runner import run_attempt, audit_attempt
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.scenes.source import resolve_target
from lastmile_dataflow.validation.lightweight import inspect_state
from helpers import fixtures, make_sim


class ConfigurationTests(unittest.TestCase):
    def test_reject_unknown_and_invalid_config(self):
        for args in ({'typo':1}, {'max_steps':0}, {'width':321}, {'penetration_ratio':float('nan')}):
            with self.assertRaises((ValueError,TypeError)):
                construct(CollectionConfig,args)
        with self.assertRaises(ValueError):
            RobotConfig('x',torso_limits=[0,2])
        with self.assertRaises(ValueError):
            TaskConfig(success_conditions={'lift':.05})

    def test_20d_layout_and_invalid_actions(self):
        c = RobotConfig('x')
        a = np.zeros(20); a[10]=a[18]=-.05; a[19]=.3
        np.testing.assert_array_equal(torso_joints(.3),[0,.3,-.6,.3,0,0])
        np.testing.assert_array_equal(validate_action(a,c),a)
        for bad in ([0]*19,[0]*21,[[0]*20],np.full(20,np.nan),np.full(20,np.inf)):
            with self.assertRaises(InvalidAction): validate_action(bad,c)
        for i,v in [(0,1),(2,1),(3,.3),(10,.01),(18,-.051),(19,.8)]:
            bad=a.copy();bad[i]=v
            with self.assertRaises(InvalidAction): validate_action(bad,c)
        a[0]=.01
        with self.assertRaises(InvalidAction): validate_action(a,c,fixed_base=True)


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.sim,self.robot=make_sim(self.root)
    def tearDown(self):
        self.sim.close();self.tmp.cleanup()

    def test_atomic_command_and_actual_controls(self):
        s=self.sim; before=s.data.ctrl.copy()
        a=s.robot.neutral_action();a[19]=.2;a[3]=.1
        command=s.robot.apply(a)
        self.assertEqual(len(command['submitted_action']),20)
        np.testing.assert_allclose(command['joint_targets']['torso'],[0,.2,-.4,.2,0,0])
        np.testing.assert_allclose(command['joint_targets']['left_arm'],[.6,0,0,-2.3,0,-.5,0])
        self.assertEqual(command['joint_targets']['head'],[0,.6])
        expected=s.data.ctrl.copy();a[18]=float('nan')
        with self.assertRaises(InvalidAction):s.robot.apply(a)
        np.testing.assert_array_equal(s.data.ctrl,expected)
        self.assertFalse(np.array_equal(before,expected))

    def test_joint_limit_rejects_before_mutation(self):
        s=self.sim;name='left_arm_0';j=s.robot.joints[name]
        s.data.qpos[s.robot.addresses[name]]=s.model.jnt_range[j,1]-.01
        before=s.data.ctrl.copy();a=s.robot.neutral_action();a[3]=.1
        with self.assertRaises(InvalidAction):s.robot.apply(a)
        np.testing.assert_array_equal(s.data.ctrl,before)

    def test_snapshot_exact_and_no_reset_after_begin(self):
        s=self.sim;frozen=self.root/'frozen';s.freeze(frozen)
        initial=s.state_vector().copy();s.data.qpos[0]+=.1;s.restore_snapshot(frozen/'initial.npz')
        np.testing.assert_array_equal(s.state_vector(),initial)
        restored=Simulation.from_snapshot(frozen,self.robot,target='target')
        np.testing.assert_array_equal(restored.state_vector(),initial)
        restored.close()
        s.begin();s.step(s.robot.neutral_action())
        self.assertAlmostEqual(s.data.time,.048)
        with self.assertRaises(RuntimeError):s.restore_snapshot(frozen/'initial.npz')
        with self.assertRaises(RuntimeError):s.freeze(self.root/'other')
        with self.assertRaises(RuntimeError):s.robot.initialize()

    def test_snapshot_tampering_blocked(self):
        frozen=self.root/'frozen';self.sim.freeze(frozen)
        with (frozen/'initial.npz').open('ab') as f:f.write(b'corrupt')
        with self.assertRaisesRegex(ValueError,'modified'):
            Simulation.from_snapshot(frozen,self.robot)

    def test_target_ambiguity_rejected(self):
        catalog=[{'instance_id':'a','asset_id':'Cup','mjcf_body':'target'},
                 {'instance_id':'b','asset_id':'Cup','mjcf_body':'robot_0/base'}]
        with self.assertRaises(ValueError): resolve_target(self.sim.model,catalog,'Cup')

    def test_planning_navigation_bridge(self):
        q=self.sim.robot.group('left_arm')+.01
        p=PlanResult('success',tuple(f'left_arm_{i}' for i in range(7)),(q.tolist(),),{})
        a=arm_waypoint_action(p,0,self.sim.robot,'left')
        np.testing.assert_allclose(a[3:10],[.01]*7)
        with self.assertRaises(ValueError):PlanResult('no_solution',p.joint_names,p.positions,{})
        with self.assertRaises(ValueError):PlanResult('success',p.joint_names,(),{})
        a=navigation_action([1,0,1],self.sim.robot)
        self.assertLessEqual(np.linalg.norm(a[:2]),.1)
        self.assertLessEqual(abs(a[2]),.2)

    def test_nonfinite_and_severe_contact_blocked(self):
        c=CollectionConfig();s=self.sim
        # target 初始中心0.15、半径0.1，无穿透；降入地板0.08m必须阻断。
        adr=int(s.model.jnt_qposadr[s.model.joint('target_free').id]);s.data.qpos[adr+2]=.02
        mujoco.mj_forward(s.model,s.data)
        self.assertFalse(inspect_state(s,c)['valid'])
        s.data.qvel[0]=np.nan
        self.assertIn('nonfinite_physics_state',[i['code'] for i in inspect_state(s,c)['issues']])


class RecordingTests(unittest.TestCase):
    def test_empty_attempt_no_video_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            r=RobotConfig('x');c=CollectionConfig(output_dir=d)
            recorder=AttemptRecorder(d,freeze_config(r,TaskConfig(),c),attempt_id='one')
            from lastmile_dataflow.validation.lightweight import independent_results
            recorder.finish('load_error','load_error',independent_results())
            self.assertEqual(read_json(recorder.path/'videos.json')['cameras'],{})
            self.assertTrue(audit_attempt(recorder.path)['valid'])
            with self.assertRaises(FileExistsError):AttemptRecorder(d,freeze_config(r,TaskConfig(),c),attempt_id='one')

    def test_missing_scene_classified(self):
        with tempfile.TemporaryDirectory() as d:
            from lastmile_dataflow.scenes.source import SceneSource
            path=run_attempt(SceneSource('missing',str(Path(d)/'none.xml')),RobotConfig('none'),
                             TaskConfig(),CollectionConfig(output_dir=d),attempt_id='missing')
            result=read_json(path/'result.json')
            self.assertEqual(result['status'],'load_error')
            self.assertEqual(result['executed_steps'],0)
            self.assertEqual(result['results']['task_completion']['status'],'unknown')

    def test_invalid_action_is_logged_no_execution_video(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d);c=CollectionConfig(output_dir=d,max_steps=2)
            with patch.object(Simulation,'render',return_value={'head_camera':np.zeros((16,16,3),dtype=np.uint8)}):
                path=run_attempt(source,robot,TaskConfig(target='target'),c,base=[0,0,0],
                                 actions=[[0]*19],attempt_id='invalid')
            result=read_json(path/'result.json')
            self.assertEqual(result['status'],'invalid_control')
            self.assertEqual(result['executed_steps'],0)
            self.assertFalse((path/'videos').exists())
            self.assertTrue(audit_attempt(path)['valid'])

    def test_executed_trajectory_continuity_and_new_attempt_restore(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d);c=CollectionConfig(output_dir=d,max_steps=2,record_video=False)
            with patch.object(Simulation,'render',return_value={'head_camera':np.zeros((16,16,3),dtype=np.uint8)}):
                path=run_attempt(source,robot,TaskConfig(target='target'),c,base=[0,0,0],attempt_id='first')
                restored=run_attempt(None,robot,TaskConfig(target='target'),c,frozen_dir=path/'scene',attempt_id='second')
            self.assertEqual(read_json(path/'result.json')['status'],'execution_complete')
            self.assertEqual(read_json(restored/'result.json')['status'],'execution_complete')
            self.assertTrue(audit_attempt(path)['valid'])
            self.assertTrue(audit_attempt(restored)['valid'])
            self.assertEqual(read_json(path/'initial_state.json'),read_json(restored/'initial_state.json'))
            rows=[json.loads(x) for x in (path/'trajectory.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertNotEqual(rows[0]['state_after']['time_s'],rows[0]['state_before']['time_s'])


if __name__=='__main__': unittest.main()
