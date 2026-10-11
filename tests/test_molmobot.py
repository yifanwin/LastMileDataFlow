"""Synthetic interface regressions, NOT evidence of real VLA task success."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from helpers import fixtures
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.robots.action import InvalidAction
from lastmile_dataflow.policies.molmobot_config import load_policy_config, upstream_defaults
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.validation.molmobot import constraint
from lastmile_dataflow.workflows.no_edit import sustained_policy_gpus

ROOT=Path(__file__).resolve().parents[1]


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); root=Path(self.tmp.name)
        source,robot=fixtures(root)
        tree=ET.parse(robot.model_path)
        for motor in tree.findall('./actuator/motor'):
            motor.set('ctrllimited','true'); motor.set('ctrlrange','-100 100')
        for body in tree.findall('.//body'):
            if 'gripper_finger_' in body.get('name',''):
                body.set('name',body.get('name').replace('gripper_finger_','ee_finger_'))
        for side in ('l','r'):
            ET.SubElement(tree.find('./worldbody/body'),'site',name='robot_0/ee_site_'+side,pos='0 0 .1')
        tree.write(robot.model_path)
        self.robot=replace(robot,control_profile='molmobot-native-v1',control_hz=10)
        self.sim=Simulation.from_source(source,self.robot,target='target')

    def tearDown(self):
        self.sim.close();self.tmp.cleanup()

    def test_native_motor_not_position_and_atomic_limits(self):
        s=self.sim
        index=s.robot.act_ids['left_finger_act']
        self.assertEqual(s.model.actuator_biastype[index],mujoco.mjtBias.mjBIAS_NONE)
        np.testing.assert_array_equal(s.model.actuator_ctrlrange[index],[-100,100])
        a=s.robot.neutral_action();a[10]=100;a[18]=-100;a[19]=.4
        result=s.robot.apply(a)
        self.assertEqual(s.data.ctrl[index],100)
        np.testing.assert_allclose(result['joint_targets']['torso'],[0,.4,-.8,.4,0,0])
        before=s.data.ctrl.copy();a[2]=float('nan')
        with self.assertRaises(InvalidAction):s.robot.apply(a)
        np.testing.assert_array_equal(before,s.data.ctrl)

    def test_upstream_projection_explicit_and_not_position_mapping(self):
        s=self.sim;a=s.robot.neutral_action();a[10]=150;a[18]=-7;a[19]=.9
        result=s.robot.apply(a)
        self.assertEqual(result['motor_targets']['left_gripper'],[100])
        self.assertEqual(result['motor_targets']['right_gripper'],[-7])
        self.assertEqual(result['submitted_action'][19],.738)
        self.assertTrue(result['adjustments'])

    def test_100ms_no_repeated_relative_accumulation(self):
        s=self.sim;s.begin();a=s.robot.neutral_action();a[0]=.02
        checks=[];before=s.robot.group('base')[0]
        command,stop=s.step_policy(a,substep_check=lambda:checks.append(s.data.time) or {'valid':True})
        self.assertIsNone(stop);self.assertEqual(len(checks),25)
        self.assertAlmostEqual(s.data.time,.1)
        self.assertAlmostEqual(command['joint_targets']['base'][0],before+.02)
        with self.assertRaises(RuntimeError):s.robot.initialize()

    def test_physics_stop_not_silently_completed(self):
        s=self.sim;s.begin()
        command,stop=s.step_policy(s.robot.neutral_action(),substep_check=lambda:{'valid':False})
        self.assertIsNotNone(stop);self.assertAlmostEqual(s.data.time,.004)

    def test_stop_transition_latches_actual_then_holds_without_drift(self):
        s=self.sim;a=np.zeros(20);a[0]=.05
        s.robot.apply(a)
        actual=s.robot.group('base').copy()
        a[0]=0
        stopped=s.robot.apply(a)
        np.testing.assert_array_equal(stopped['joint_targets']['base'],actual)
        # Synthetic measured drift is pre-begin only, testing controller state.
        s.data.qpos[s.robot.addresses['base_x']]+=.01
        again=s.robot.apply(a)
        np.testing.assert_array_equal(again['joint_targets']['base'],actual)
        before=s.data.ctrl.copy();cache={k:v.copy() for k,v in s.robot._native_stationary_targets.items()}
        a[3]=.3
        with self.assertRaises(InvalidAction):s.robot.apply(a)
        np.testing.assert_array_equal(s.data.ctrl,before)
        for k,v in cache.items():np.testing.assert_array_equal(s.robot._native_stationary_targets[k],v)

    def test_rejected_prediction_retained_without_fake_execution(self):
        import json
        from types import SimpleNamespace
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.io import read_json
        from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
        root=Path(self.tmp.name)/'run';self.sim.freeze(root/'scene')
        config=replace(load_no_edit_config(ROOT/'configs/no_edit/molmobot.json'),third_person_enabled=False)
        pc=replace(load_policy_config(ROOT/'configs/policies/molmobot.json'),task_horizon=1)
        a=np.zeros(20);a[0]=.2
        response={'action':a,'raw_action':a.copy(),'processed_images':[],
                  'conditioning_points':None,'model_prompt':'Pick up the cup.'}
        policy=SimpleNamespace(config=pc,metadata={},action=lambda *args,**kwargs:response)
        frames={k:np.zeros((16,16,3),dtype=np.uint8) for k in self.sim.robot.camera_names}
        task={'operation':'pick','target_body':'target','anchor_world':[2,0,.15],
              'category':'Cup','instance_id':'target','task_id':'pick-fixture'}
        station={'station_id':'S0000','base':[0,0,0],'xy':[0,0]}
        with patch('lastmile_dataflow.runtime.molmobot_execution.clone_sim',return_value=self.sim), \
             patch('lastmile_dataflow.runtime.molmobot_execution.place_frozen_station'), \
             patch.object(self.sim,'render',return_value=frames):
            result=run_vla_attempt(self.sim,task,station,config,CollectionConfig(record_video=False),
                                   root,'rejected',seed=1,policy=policy)
        p=Path(result['attempt'])
        self.assertEqual((result['status'],result['executed_steps']),('failure',0))
        request=json.loads((p/'policy_requests.jsonl').read_text())
        self.assertEqual(request['raw_action'][0],.2)
        self.assertEqual((p/'policy_actions.jsonl').read_text(),'')
        self.assertEqual(read_json(p/'videos.json')['cameras'],{})

    def test_vla_contact_record_only_and_simulation_failure_separate(self):
        from types import SimpleNamespace
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.io import read_json
        from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
        from test_contact_policy import CONTACT
        root=Path(self.tmp.name)/'run';self.sim.freeze(root/'scene')
        config=replace(load_no_edit_config(ROOT/'configs/no_edit/molmobot.json'),third_person_enabled=False)
        pc=replace(load_policy_config(ROOT/'configs/policies/molmobot.json'),task_horizon=2)
        def action(*args,**kwargs):
            a=np.zeros(20);a[10]=a[18]=100
            return {'action':a,'raw_action':a.copy(),'processed_images':[],
                    'conditioning_points':None,'model_prompt':'Pick up the cup.'}
        policy=SimpleNamespace(config=pc,metadata={},action=action)
        frames={k:np.zeros((16,16,3),dtype=np.uint8) for k in self.sim.robot.camera_names}
        task={'operation':'pick','target_body':'target','anchor_world':[0,0,.15],
              'category':'Cup','instance_id':'target','task_id':'pick-fixture'}
        station={'station_id':'S0000','base':[0,0,0],'xy':[0,0]}
        with patch('lastmile_dataflow.runtime.molmobot_execution.clone_sim',return_value=self.sim), \
             patch('lastmile_dataflow.runtime.molmobot_execution.place_frozen_station'), \
             patch('lastmile_dataflow.runtime.molmobot_execution.contact_observations',return_value=[CONTACT]), \
             patch.object(self.sim,'render',return_value=frames):
            result=run_vla_attempt(self.sim,task,station,config,CollectionConfig(record_video=False),
                                   root,'contacts',seed=1,policy=policy)
        self.assertEqual(result['executed_steps'],2,result)
        self.assertAlmostEqual(read_json(Path(result['attempt'])/'final_state.json')['time_s'],.2)
        self.assertEqual(result['status'],'failure')  # no lift, NOT a collision failure
        self.assertTrue(result['trajectory_metrics']['simulation_valid'])
        self.assertEqual(result['trajectory_metrics']['forbidden_contact_count'],50)
        self.assertNotEqual(result['attribution']['category'],'Collision')
        import json
        rows=[json.loads(line) for line in (Path(result['attempt'])/'policy_actions.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['submitted_action'][10]==r['raw_action'][10]==100 for r in rows))

    def test_vla_simulation_failure_has_unknown_task_result(self):
        from types import SimpleNamespace
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.io import read_json
        from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
        root=Path(self.tmp.name)/'run';self.sim.freeze(root/'scene')
        config=replace(load_no_edit_config(ROOT/'configs/no_edit/molmobot.json'),third_person_enabled=False)
        pc=replace(load_policy_config(ROOT/'configs/policies/molmobot.json'),task_horizon=2)
        def action(*args,**kwargs):
            a=np.zeros(20)
            return {'action':a,'raw_action':a.copy(),'processed_images':[],
                    'conditioning_points':None,'model_prompt':'Pick up the cup.'}
        policy=SimpleNamespace(config=pc,metadata={},action=action)
        frames={k:np.zeros((16,16,3),dtype=np.uint8) for k in self.sim.robot.camera_names}
        task={'operation':'pick','target_body':'target','anchor_world':[0,0,.15],
              'category':'Cup','instance_id':'target','task_id':'pick-fixture'}
        station={'station_id':'S0000','base':[0,0,0],'xy':[0,0]}
        # Inject a MuJoCo warning counter: the finite reset state must not hide it.
        self.sim.data.warning[int(mujoco.mjtWarning.mjWARN_BADQACC)].number=1
        with patch('lastmile_dataflow.runtime.molmobot_execution.clone_sim',return_value=self.sim), \
             patch('lastmile_dataflow.runtime.molmobot_execution.place_frozen_station'), \
             patch.object(self.sim,'render',return_value=frames):
            result=run_vla_attempt(self.sim,task,station,config,CollectionConfig(record_video=False),
                                   root,'sim-warning',seed=1,policy=policy)
        self.assertEqual(result['status'],'simulation_failure')
        self.assertFalse(result['trajectory_metrics']['simulation_valid'])
        self.assertIsNone(result['trajectory_metrics']['task_success'])
        self.assertEqual(result['executed_steps'],1)
        self.assertEqual(read_json(Path(result['attempt'])/'result.json')['results']['task_completion']['status'],'unknown')


class ConfigTests(unittest.TestCase):
    def test_model_defaults_are_from_selected_sim_configs(self):
        config=load_policy_config(ROOT/'configs/policies/molmobot.json')
        if not Path(config.source_dir).is_dir():self.skipTest('selected external model source not installed')
        values=upstream_defaults(config.source_dir)
        self.assertFalse(values['pick']['clamp_gripper'])
        self.assertTrue(values['open']['clamp_gripper'])
        self.assertEqual(values['open']['gripper_threshold'],5)
        self.assertEqual(sum(values['pick']['state_spec'].values()),22)
        self.assertEqual(values['open']['max_conditioning_points'],1)
        sampling=load_no_edit_config(ROOT/'configs/no_edit/molmobot.json')
        self.assertEqual((sampling.radius_m,sampling.trials_per_station),(2.,5))
        self.assertEqual(sampling.operation_base_mode,'policy_relative')

    def test_native_resolution_and_backend_mode_fail_closed(self):
        config=load_no_edit_config(ROOT/'configs/no_edit/molmobot.json')
        for values in ({'width':320},{'operation_base_mode':'holonomic_joint_planning'}):
            with self.assertRaises(ValueError):replace(config,**values)

    def test_open_point_must_survive_crop_and_actual_chunk_state_logged(self):
        from lastmile_dataflow.policies.molmobot_service import TraceAgent
        from unittest.mock import Mock
        underlying=Mock();underlying.get_action_chunk.return_value=np.zeros((16,20))
        trace=TraceAgent(underlying);trace.require_point=True
        with self.assertRaisesRegex(ValueError,'outside_fisheye_crop'):
            trace.get_action_chunk(state=np.zeros(22),images=[],task_description='Open the door.')
        underlying.get_action_chunk.assert_not_called()
        trace.get_action_chunk(state=np.arange(22),images=[],task_description='<points coords="1 1 500 500">door</points>')
        np.testing.assert_array_equal(trace.state,np.arange(22))
        self.assertEqual(trace.calls,1)

    def test_old_position_snapshot_profile_default_does_not_allow_native(self):
        from lastmile_dataflow.io import read_json,write_json,digest,file_digest
        from lastmile_dataflow.runtime.build_session import model_identity,checkpoint_identity
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d);sim=Simulation.from_source(source,robot,target='target')
            frozen=Path(d)/'frozen';sim.freeze(frozen)
            v=read_json(frozen/'version.json');del v['robot']['control_profile']
            v['version_id']=digest({k:x for k,x in v.items() if k!='version_id'})
            write_json(frozen/'version.json',v)
            checksums=read_json(frozen/'checksums.json');checksums['version.json']=file_digest(frozen/'version.json')
            write_json(frozen/'checksums.json',checksums)
            restored=Simulation.from_snapshot(frozen,robot);restored.close()
            with self.assertRaisesRegex(ValueError,'robot configuration mismatch'):
                Simulation.from_snapshot(frozen,replace(robot,control_profile='molmobot-native-v1'))
            # v2 additionally binds model/config/checkpoint identities. Old
            # identities must stay intact rather than being re-hashed with defaults.
            v['schema_version']='2.0'
            v['model_id']=model_identity(sim,robot_config_payload=v['robot'])
            v['checkpoint_id']=checkpoint_identity(sim,v['model_id'])
            v['mapping_id']=digest(sim.catalog)
            v['version_id']=digest({k:x for k,x in v.items() if k!='version_id'})
            write_json(frozen/'version.json',v)
            checksums['version.json']=file_digest(frozen/'version.json')
            write_json(frozen/'checksums.json',checksums)
            restored=Simulation.from_snapshot(frozen,robot);restored.close();sim.close()

    def test_sustained_gpu_requires_all_samples_and_enough_memory(self):
        config=load_policy_config(ROOT/'configs/policies/molmobot.json')
        rows=[[{'index':0,'utilization':0,'free_mb':30000}, {'index':2,'utilization':5,'free_mb':32000}],
              [{'index':2,'utilization':8,'free_mb':31000}],
              [{'index':0,'utilization':0,'free_mb':30000}, {'index':2,'utilization':1,'free_mb':33000}]]
        with patch('lastmile_dataflow.workflows.no_edit.available_gpus',side_effect=rows),patch('time.sleep'):
            devices=sustained_policy_gpus(list(range(8)),config)
        self.assertEqual([r['index'] for r in devices],[2])
        self.assertEqual((devices[0]['utilization'],devices[0]['free_mb']),(8,31000))

    def test_two_commanded_arms_not_idle_drift_but_head_still_checked(self):
        initial={'base':[0,0,0],'head':[0,0],'idle_arm':[0]*7,'target':'obj',
                 'workspace_center_xy':[0,0],'workspace_radius_m':2}
        row={'base':[1,0,0],'head':[0,0],'idle_arm':[.5]*7,'target':'obj',
             'torso':[0]*6,'command_h':.7,'phase':'vla_operation','illegal':[]}
        self.assertIsNone(constraint(row,initial))
        row['head']=[.1,0];self.assertEqual(constraint(row,initial),'head_drift')
        row['base']=[3,0,0];self.assertEqual(constraint(row,initial),'base_outside_workspace')

    def test_vla_complete_sampling_does_not_call_grasp_or_navigation(self):
        from unittest.mock import Mock
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.navigation.astar import Grid
        from lastmile_dataflow.io import write_json,read_json
        from lastmile_dataflow.workflows.no_edit import collect_task
        config=load_no_edit_config(ROOT/'configs/no_edit/molmobot.json')
        with tempfile.TemporaryDirectory() as d:
            scene=Path(d)/'scenes/val_103';scene.mkdir(parents=True)
            write_json(scene/'robot_geometry.json',{'radius_m':.4})
            station={'station_id':'S0000','xy':[0,0],'geometry':'valid'}
            task={'task_id':'pick-fixture','instance_id':'Cup_30','category':'Cup','operation':'pick',
                  'target_body':'Cup_30','anchor_world':[0,0,1],'instruction':'synthetic, not real success'}
            grid=Grid(np.zeros(2),.1,np.ones((3,3),bool))
            with patch('mujoco.Renderer'),patch('lastmile_dataflow.runtime.no_edit_execution.clone_sim'), \
                 patch('lastmile_dataflow.stations.no_edit_sampling.disk_samples',return_value=[station]), \
                 patch('lastmile_dataflow.stations.no_edit_sampling.initialize_station',return_value=station), \
                 patch('lastmile_dataflow.navigation.astar.task_support_obstacles',return_value=[]), \
                 patch('lastmile_dataflow.navigation.astar.scene_grid',return_value=grid), \
                 patch('lastmile_dataflow.exporting.no_edit_heatmap.export_heatmap'), \
                 patch('lastmile_dataflow.exporting.navigation_map.export_navigation_map'), \
                 patch('lastmile_dataflow.workflows.no_edit.check_disk'), \
                 patch('lastmile_dataflow.tasks.raw_scene.grasp_candidates') as grasps, \
                 patch('lastmile_dataflow.navigation.astar.search') as navigate, \
                 patch('lastmile_dataflow.runtime.molmobot_execution.run_vla_attempt',return_value={
                     'station_id':'S0000','attempt':'synthetic','status':'success'}) as execute:
                result=collect_task(Mock(),task,d,config,CollectionConfig(output_dir=d),scene,policy=Mock())
            self.assertEqual(execute.call_count,5)
            grasps.assert_not_called();navigate.assert_not_called()
            self.assertEqual(result['status'],'station_evaluation_complete')
            self.assertFalse(result['dataset_eligible'])
            rows=read_json(scene/'tasks/cup30/station_statistics.json')
            self.assertEqual((rows[0]['terminal_trials'],rows[0]['successes']),(5,5))


if __name__=='__main__':unittest.main()
