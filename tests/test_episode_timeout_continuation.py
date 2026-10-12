"""Synthetic scheduling regressions, not real VLA task-success evidence."""
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.navigation.astar import Grid
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.stations.no_edit_statistics import aggregate,construction_rate,is_episode_terminal
from lastmile_dataflow.workflows.no_edit import collect_task


class EpisodeTimeoutTests(unittest.TestCase):
    def collect(self,backend,first_status='timeout',reason='attempt_wall_clock_budget',resume=False,max_trials=None):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'val_0';tp=root/'tasks/t';tp.mkdir(parents=True)
            write_json(root/'robot_geometry.json',{'radius_m':.4})
            stations=[{'station_id':k,'xy':xy,'geometry':'valid'} for k,xy in [('a',[0,0]),('b',[0,1])]]
            write_json(tp/'stations.json',stations)
            task={'task_id':'t','output_name':'t','operation':'pick','target_body':'target','anchor_world':[0,0,1],'instruction':'synthetic'}
            config=NoEditConfig() if backend=='curobo' else NoEditConfig(operation_backend='molmobot',policy_config='synthetic',operation_base_mode='policy_relative',width=1024,height=576)
            calls=[]
            def execute(*args,**kwargs):
                calls.append(args[2]['station_id'])
                return {'status':first_status if len(calls)==1 else 'failure',
                        'reason':reason if len(calls)==1 else 'synthetic_failure',
                        'station_id':args[2]['station_id'],'attempt':'synthetic','attribution':{'category':'Synthetic'}}
            grid=Grid(np.array([-1.,-1.]),1.,np.ones((6,6),bool))
            target='lastmile_dataflow.runtime.'+('molmobot_execution.run_vla_attempt' if backend=='molmobot' else 'no_edit_execution.run_raw_attempt')
            with patch('lastmile_dataflow.tasks.raw_scene.grasp_candidates',return_value=[]), \
                 patch('lastmile_dataflow.navigation.astar.task_support_obstacles',return_value=[]), \
                 patch('lastmile_dataflow.navigation.astar.scene_grid',return_value=grid), \
                 patch('lastmile_dataflow.exporting.no_edit_heatmap.export_heatmap'), \
                 patch('lastmile_dataflow.exporting.navigation_map.export_navigation_map'), \
                 patch('lastmile_dataflow.workflows.no_edit.check_disk'), \
                 patch(target,side_effect=execute):
                result=collect_task(None,task,d,config,CollectionConfig(output_dir=d),root,policy=object(),max_trials=max_trials)
                # Force a normal incomplete-result resume path without changing trials.
                if resume:
                    write_json(tp/'result.json',{**result,'status':'incomplete'})
                    collect_task(None,task,d,config,CollectionConfig(output_dir=d),root,policy=object())
            return result,calls,read_json(tp/'progress.json'),read_json(tp/'trials.json')

    def test_timeout_continues_same_station_and_next_station_both_backends(self):
        for backend in ('curobo','molmobot'):
            with self.subTest(backend=backend):
                result,calls,progress,trials=self.collect(backend)
                self.assertEqual(calls,['a']*5+['b']*5)
                self.assertEqual(progress['terminal_trials'],10)
                self.assertEqual(progress['timeout_trials'],1)
                self.assertEqual(trials[0]['status'],'timeout')
                self.assertIsNone(result['collection_error'])
                self.assertEqual(result['timeout_trials'],1)
                self.assertNotEqual(result['status'],'incomplete')

    def test_resume_does_not_retry_new_or_legacy_timeout(self):
        for status in ('timeout','incomplete'):
            result,calls,progress,trials=self.collect('molmobot',first_status=status,resume=True)
            self.assertEqual(len(calls),10)
            self.assertEqual(trials[0]['status'],status) # no evidence rewrite
            self.assertEqual(progress['timeout_trials'],1)

    def test_interruption_and_infrastructure_errors_still_stop(self):
        for status,reason in [('incomplete','interrupted'),('infrastructure_error','model connection lost')]:
            result,calls,_,_=self.collect('molmobot',first_status=status,reason=reason)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['collection_error'],status)

    def test_explicit_global_trial_budget_still_stops(self):
        result,calls,_,_=self.collect('molmobot',max_trials=1)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['collection_error'],'incomplete')

    def test_timeout_statistics_are_separate_and_deduplicated(self):
        s={'station_id':'a','geometry':'valid','xy':[0,0]}
        rows=[{'station_id':'a','trial':0,'status':'incomplete','reason':'attempt_wall_clock_budget','attempt':'old'},
              {'station_id':'a','trial':1,'status':'timeout','reason':'attempt_wall_clock_budget','attempt':'timeout'},
              {'station_id':'a','trial':2,'status':'success','attempt':'success'}]
        stats=aggregate([s],rows,3)[0]
        self.assertEqual((stats['terminal_trials'],stats['timeout_trials'],stats['successes']),(3,2,1))
        self.assertEqual(stats['success_rate'],1/3)
        self.assertEqual(construction_rate([stats],3,.05)['timeout_trials'],2)
        retry={**rows[0],'status':'success','attempt':'retry'}
        self.assertEqual(aggregate([s],rows+[retry],3)[0]['terminal_trials'],3)
        self.assertFalse(is_episode_terminal({'status':'incomplete','reason':'interrupted'}))

    def test_timeout_has_distinct_status_directory(self):
        from lastmile_dataflow.exporting.no_edit_layout import index_attempt
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'val_0/attempts/pick/a/test-timeout';p.mkdir(parents=True)
            status_path=index_attempt(p,'timeout')
            self.assertIn('/timeout/',str(status_path))
