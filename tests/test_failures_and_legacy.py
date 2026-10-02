import copy
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from helpers import fixtures
from lastmile_dataflow.config import CollectionConfig, TaskConfig
from lastmile_dataflow.integrations.legacy import convert_episode
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.runtime.runner import run_attempt, audit_attempt
from lastmile_dataflow.runtime.simulation import Simulation


class FailureTests(unittest.TestCase):
    def test_illegal_robot_initialization_is_not_load_error(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d)
            initial=copy.deepcopy(robot.initial);initial['torso']=[2]
            robot=replace(robot,initial=initial)
            p=run_attempt(source,robot,TaskConfig(),CollectionConfig(output_dir=d),base=[0,0,0])
            self.assertEqual(read_json(p/'result.json')['status'],'invalid_initialization')
            self.assertEqual(read_json(p/'result.json')['executed_steps'],0)
            self.assertTrue(audit_attempt(p)['valid'])

    def test_invalid_scene_initialization_keeps_model_and_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d)
            Path(source.xml_path).write_text(Path(source.xml_path).read_text().replace('2 0 .15','2 0 .02'))
            p=run_attempt(source,robot,TaskConfig(),CollectionConfig(output_dir=d),base=[0,0,0])
            result=read_json(p/'result.json')
            self.assertEqual(result['status'],'invalid_initialization')
            self.assertEqual(result['results']['scene_validity']['status'],'invalid')
            self.assertTrue((p/'scene'/'initial.npz').exists())
            self.assertFalse((p/'videos').exists())
            self.assertTrue(audit_attempt(p)['valid'])

    def test_render_failure_does_not_erase_executed_action(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d)
            with patch.object(Simulation,'render',side_effect=[{'head_camera':np.zeros((16,16,3),dtype=np.uint8)},RuntimeError('renderer down')]):
                p=run_attempt(source,robot,TaskConfig(),CollectionConfig(output_dir=d),base=[0,0,0])
            self.assertEqual(read_json(p/'result.json')['status'],'infrastructure_error')
            self.assertEqual(read_json(p/'result.json')['executed_steps'],1)
            self.assertEqual(len((p/'trajectory.jsonl').read_text().splitlines()),1)
            self.assertTrue((p/'final_snapshot.npz').exists())
            self.assertTrue(audit_attempt(p)['valid'])

    def test_no_action_means_no_execution_video(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d)
            with patch.object(Simulation,'render',return_value={'head_camera':np.zeros((16,16,3),dtype=np.uint8)}):
                p=run_attempt(source,robot,TaskConfig(),CollectionConfig(output_dir=d),base=[0,0,0],actions=[])
            self.assertEqual(read_json(p/'result.json')['executed_steps'],0)
            self.assertEqual(read_json(p/'videos.json')['cameras'],{})

    def test_nonfinite_state_has_explicit_nullable_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            source,robot=fixtures(d);sim=Simulation.from_source(source,robot)
            sim.data.qvel[0]=np.nan
            state=sim.observe_state()
            self.assertIsNone(state['qvel'][0])
            self.assertIn('.qvel[0]',state['nonfinite_fields'])
            write_json(Path(d)/'state.json',state)
            sim.close()


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        source,self.robot=fixtures(self.root)
        self.assets=self.root/'assets';scene_dir=self.assets/'scenes'/'procthor-10k-val'
        scene_dir.mkdir(parents=True)
        shutil.copyfile(source.xml_path,scene_dir/'val_103.xml')
        write_json(scene_dir/'val_103_metadata.json',{'objects':{}})
        init=copy.deepcopy(self.robot.initial);init['torso']=[0]*6
        init['left_gripper']=init['right_gripper']=[-.05,.05]
        self.episode={'house_index':103,'data_split':'val','robot':{'robot_name':'rby1m','init_qpos':init},
                      'task':{'pickup_obj_name':'target'},
                      'scene_modifications':{'added_objects':{},'object_poses':{'target':[2,0,.15,1,0,0,0]}}}
        self.file=self.root/'episode.json';write_json(self.file,self.episode)
    def tearDown(self):self.tmp.cleanup()

    def test_conversion_and_restoration_without_old_imports(self):
        record=convert_episode(self.file,self.assets,self.root/'imported.json')
        self.assertEqual(record['restoration']['robot_initial']['torso'],[0])
        self.assertEqual(record['provenance']['import_kind'],'initial_state_only_no_success_or_approval')
        from lastmile_dataflow.scenes.source import SceneSource
        sim=Simulation.from_source(SceneSource(**record['source']),self.robot,target='target',restoration=record['restoration'])
        self.assertEqual(sim.observe_state()['target']['pose_world_xyz_wxyz'][:3],[2,0,.15])
        sim.close()

    def test_reject_unsupported_deletion_and_uncoupled_torso(self):
        for field,value in [('deletion',['target']),('torso',[0,.2,0,0,0,0])]:
            e=copy.deepcopy(self.episode)
            if field=='deletion':e['scene_modifications']['removed_objects']=value
            else:e['robot']['init_qpos']['torso']=value
            write_json(self.file,e)
            with self.assertRaises(ValueError):convert_episode(self.file,self.assets,self.root/(field+'.json'))

    def test_asset_lexical_escape_rejected_but_nas_style_symlink_allowed(self):
        e=copy.deepcopy(self.episode);e['scene_modifications']['added_objects']={'added':'../outside.xml'}
        write_json(self.file,e)
        with self.assertRaisesRegex(ValueError,'escapes'):convert_episode(self.file,self.assets,self.root/'bad.json')
        real=self.root/'cached.xml';real.write_text('<mujoco/>')
        (self.assets/'cached.xml').symlink_to(real)
        e['scene_modifications']['added_objects']={'added':'cached.xml'};write_json(self.file,e)
        result=convert_episode(self.file,self.assets,self.root/'good.json')
        self.assertEqual(result['restoration']['added_objects']['added']['path'],str(self.assets/'cached.xml'))


if __name__=='__main__':unittest.main()
