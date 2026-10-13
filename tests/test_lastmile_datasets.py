"""Synthetic/mocked orchestration contracts, NOT real CUDA recovery evidence."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.config import CollectionConfig, RobotConfig
from lastmile_dataflow.io import digest, file_digest, read_json, write_json
from lastmile_dataflow.navigation.astar import Grid
from lastmile_dataflow.stations.lastmile_selection import accepted_trials, recovery_choices, station_calibration, recover_trial_records, recover_rollout_records
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.workflows.lastmile_datasets import collect, collect_views, input_units, phase_ranges, write_rows
from lastmile_dataflow.workflows.no_edit import collect_task
from lastmile_dataflow.runtime.no_edit_execution import navigate, OperationFailure


def stations():
    return [{'station_id':'low','geometry':'valid','xy':[0.,0.],'base':[0.,0.,0.]},
            {'station_id':'high','geometry':'valid','xy':[.4,0.],'base':[.4,0.,0.]}]


def trials():
    return [{'station_id':s['station_id'],'trial':i,'status':'success' if s['station_id']=='high' else 'failure',
             'attempt':'synthetic-'+s['station_id']+str(i),'control':{'seed':1},'seed':i,
             'attribution':{'category':'fixture'}} for s in stations() for i in range(5)]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.config = NoEditConfig(lastmile_selection=True,scene_package='fixture')
        self.binding = {'sample_id':'fixture','robot_sensor_config_id':'position'}

    def test_lastmile_candidate_limits(self):
        for kw in ({'start_count':4},{'max_s1_candidates':6}):
            with self.assertRaises(ValueError):
                NoEditConfig(lastmile_selection=True,scene_package='fixture',**kw)
        self.assertEqual(NoEditConfig(max_s1_candidates=6).max_s1_candidates,6)

    def test_existing_benchmark_rule_used_and_count_boundaries(self):
        rows = trials()
        rows[0]['status']='success'
        rows[-1]['status']='failure'
        cal=station_calibration(stations(),rows,5,self.binding,self.config)
        choices=recovery_choices(stations(),cal,self.binding,self.config)
        self.assertEqual([(s['station_id'],[g['station_id'] for g in gs]) for s,gs,_ in choices],[('low',['high'])])

    def test_errors_and_partial_map_cannot_create_formal_pair(self):
        rows=trials(); rows[0]['status']='recording_error'
        cal=station_calibration(stations(),rows,5,self.binding,self.config)
        self.assertFalse(cal['complete']); self.assertEqual(cal['stations'][0]['trials'],4)
        self.assertEqual(recovery_choices(stations(),cal,self.binding,self.config),[])

    def test_error_then_valid_retry_preserved_but_counted_once(self):
        rows=trials(); error={**rows[0],'status':'infrastructure_error','attempt':'old-invalid-fixture'}
        cal=station_calibration(stations(),[error]+rows,5,self.binding,self.config)
        self.assertTrue(cal['complete']); self.assertEqual(cal['stations'][0]['trials'],5)
        self.assertEqual(cal['stations'][0]['error_attempts'],1)
        self.assertEqual([r['accepted'] for r in cal['stations'][0]['attempts'][:2]],[False,True])

    def test_duplicate_valid_trial_and_foreign_station_refused(self):
        for rows in (trials()+[trials()[0]],trials()+[{**trials()[0],'station_id':'foreign'}],
                     [{**trials()[0],'trial':5}]):
            with self.assertRaises(ValueError): accepted_trials(stations(),rows,5)

    def test_selected_pose_or_config_mismatch_refused(self):
        cal=station_calibration(stations(),trials(),5,self.binding,self.config)
        wrong=stations(); wrong[0]['base'][2]=1
        with self.assertRaisesRegex(ValueError,'stations mismatch'):
            recovery_choices(wrong,cal,self.binding,self.config)
        with self.assertRaisesRegex(ValueError,'binding mismatch'):
            recovery_choices(stations(),cal,{**self.binding,'robot_sensor_config_id':'native'},self.config)

    def test_far_or_no_low_high_pair_excluded(self):
        far=stations(); far[1]['xy']=[3.,0.]
        cal=station_calibration(far,trials(),5,self.binding,self.config)
        self.assertEqual(recovery_choices(far,cal,self.binding,self.config),[])

    def test_config_opt_in_validation_and_legacy_defaults(self):
        self.assertFalse(NoEditConfig().lastmile_selection)
        for kw in ({'lastmile_selection':True},{'lastmile_low':.8,'lastmile_high':.2},
                   {'lastmile_low':float('nan')},{'lastmile_nearby_m':0}):
            with self.assertRaises(ValueError): NoEditConfig(**kw)

    def test_committed_orphan_attempts_reconciled_without_reexecution(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); path={'xy':[[0,0],[.4,0]]}
            for name,route in [('pick-t-low-t0-abc123',None),('pick-t-rollout-low-high-abc456',path)]:
                attempt=root/'attempts/pick/low'/name
                write_json(attempt/'config.json',{'task':{'task_id':'t'},'station':stations()[0],'goal':stations()[1],
                           'protocol':asdict(self.config),'seed':1,'path':route})
                write_json(attempt/'outcome.json',{'status':'success','navigation':{'status':'success'},'continuous_simulation':True})
                write_json(attempt/'result.json',{'status':'success'})
                write_json(attempt/'episode.json',{'schema_version':'research-episode-v1', 'status':'complete',
                    'termination_status':'success', 'episode_id':attempt.name, 'required_files':[], 'config_sha256':digest(read_json(attempt/'config.json'))})
            recovered=recover_trial_records(root,{'task_id':'t'},stations(),[],self.config)
            self.assertEqual(len(recovered),1); self.assertEqual(recovered[0]['trial'],0)
            selection=recover_rollout_records(root,{'task_id':'t'},{'successful':[],'failed_rollouts':[]},recovered,self.config)
            self.assertEqual(len(selection['successful']),1)
            self.assertEqual(len(recover_trial_records(root,{'task_id':'t'},stations(),recovered,self.config)),1)
            self.assertEqual(len(recover_rollout_records(root,{'task_id':'t'},selection,recovered,self.config)['successful']),1)


class CollectorTests(unittest.TestCase):
    def run_fixture(self, root, *, calibration_only=False, trial_rows=None, outcome='success',
                    path=True, max_trials=None, station_rows=None, start_count=1):
        tp=root/'tasks'/'t'; tp.mkdir(parents=True,exist_ok=True)
        write_json(root/'robot_geometry.json',{'radius_m':.4})
        write_json(root/'scene_dataset_input/input_manifest.json',
                   {'scene_dataset_id':'fixture-dataset','sample_id':'sample','package_identity':'fixture-sha',
                    'robot_sensor_config_id':'position'})
        (root/'scene').mkdir(exist_ok=True); (root/'scene/model.mjb').write_bytes(b'SYNTHETIC identity, not a real model')
        write_json(tp/'stations.json',station_rows or stations())
        if trial_rows is not None: write_json(tp/'trials.json',trial_rows)
        task={'task_id':'t','operation':'pick','target_body':'target','anchor_world':[0.,0.,1.],
              'instruction':'synthetic fixture only','output_name':'t'}
        config=NoEditConfig(scene_package='fixture',lastmile_selection=True,discard_below=.99,start_count=start_count)
        grid=Grid(np.array([-1.,-1.]),1.,np.ones((6,6),bool))
        calls=[]
        def attempt(*args,**kw):
            calls.append(kw)
            status=outcome(len(calls)) if callable(outcome) else outcome
            return {'station_id':args[2]['station_id'],'status':status,'attempt':str(root/('fixture-attempt-'+str(len(calls)))),
                    'navigation':{'status':'success'},'control':{'seed':1},'attribution':{'category':'fixture'},
                    'continuous_simulation':True}
        with patch('lastmile_dataflow.workflows.no_edit.prepare_execution_stations'), \
             patch('lastmile_dataflow.workflows.no_edit.check_disk'), \
             patch('lastmile_dataflow.tasks.raw_scene.grasp_candidates',return_value=[]), \
             patch('lastmile_dataflow.navigation.astar.task_support_obstacles',return_value=[]), \
             patch('lastmile_dataflow.navigation.astar.scene_grid',return_value=grid), \
             patch('lastmile_dataflow.navigation.astar.search',return_value={'xy':[[0.,0.],[.4,0.]]} if path else None), \
             patch('lastmile_dataflow.exporting.no_edit_heatmap.export_heatmap'), \
             patch('lastmile_dataflow.exporting.navigation_map.export_navigation_map'), \
             patch('lastmile_dataflow.runtime.no_edit_execution.run_raw_attempt',side_effect=attempt):
            result=collect_task(None,task,root,config,CollectionConfig(output_dir=str(root)),root,
                                calibration_only=calibration_only,max_trials=max_trials)
        return result,calls

    def test_global_discard_not_applied_and_resume_keeps_success(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            result,calls=self.run_fixture(root,trial_rows=trials())
            self.assertEqual(result['status'],'retained'); self.assertEqual(len(calls),1)
            self.assertFalse(result['rate']['global_discard_applied'])
            result,calls=self.run_fixture(root)
            self.assertEqual(len(calls),0); self.assertEqual(result['successful_rollouts'],1)

    def multi_rows(self, lows=1, highs=6):
        ss=[{'station_id':f'low{i}','geometry':'valid','xy':[0.,i*.01],
             'base':[0.,i*.01,0.]} for i in range(lows)]
        ss += [{'station_id':f'high{i}','geometry':'valid','xy':[.1+i*.05,0.],
                'base':[.1+i*.05,0.,0.]} for i in range(highs)]
        ts=[{'station_id':st['station_id'],'trial':i,
             'status':'success' if st['station_id'].startswith('high') else 'failure',
             'attempt':f'synthetic-{st["station_id"]}-{i}','control':{'seed':1},'seed':i}
            for st in ss for i in range(5)]
        return ss,ts

    def test_multiple_goals_cap_and_pair_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); ss,ts=self.multi_rows()
            result,calls=self.run_fixture(root,station_rows=ss,trial_rows=ts)
            self.assertEqual(len(calls),5)
            self.assertEqual(result['start_groups'][0]['successful_s1_count'],5)
            self.assertEqual(result['start_groups'][0]['warnings'],[])
            # Simulate interruption after the first pair was registered.
            selection=read_json(root/'tasks/t/selection.json')
            selection['successful']=selection['successful'][:1]
            write_json(root/'tasks/t/selection.json',selection)
            (root/'tasks/t/result.json').unlink()  # crash before final task result
            result,calls=self.run_fixture(root,station_rows=ss)
            self.assertEqual(len(calls),4)
            result,calls=self.run_fixture(root,station_rows=ss)
            self.assertEqual(len(calls),0)
            self.assertEqual(result['successful_rollouts'],5)

    def test_infrastructure_resume_keeps_success_and_retries_pending_pair(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); ss,ts=self.multi_rows(highs=3)
            result,calls=self.run_fixture(root,station_rows=ss,trial_rows=ts,
                                          outcome=lambda n:'success' if n==1 else 'infrastructure_error')
            self.assertEqual(result['status'],'infrastructure_error')
            self.assertEqual(len(calls),2)
            result,calls=self.run_fixture(root,station_rows=ss)
            self.assertEqual(len(calls),2)
            self.assertEqual(result['successful_rollouts'],3)
            self.assertEqual(len(read_json(root/'tasks/t/selection.json')['failed_rollouts']),1)

    def test_no_eligible_start_does_not_execute_rollout(self):
        with tempfile.TemporaryDirectory() as d:
            ss,ts=self.multi_rows(lows=0,highs=2)
            result,calls=self.run_fixture(Path(d),station_rows=ss,trial_rows=ts,start_count=3)
            self.assertEqual(result['status'],'no_eligible_pair')
            self.assertEqual(calls,[])

    def test_three_starts_each_collect_multiple_goals(self):
        with tempfile.TemporaryDirectory() as d:
            ss,ts=self.multi_rows(lows=4,highs=2)
            result,calls=self.run_fixture(Path(d),station_rows=ss,trial_rows=ts,start_count=3)
            self.assertEqual(len(calls),6)
            self.assertEqual(len(result['start_groups']),3)
            self.assertTrue(all(g['successful_s1_count']==2 for g in result['start_groups']))

    def test_single_solution_warning_does_not_block_retention(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_fixture(Path(d),trial_rows=trials(),start_count=3)
            self.assertTrue(result['dataset_eligible'])
            self.assertEqual(result['warnings'],['fewer_than_preferred_starts'])
            self.assertEqual(result['start_groups'][0]['warnings'],['single_successful_solution'])

    def test_terminal_pair_failures_not_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); ss,ts=self.multi_rows(highs=2)
            result,calls=self.run_fixture(root,station_rows=ss,trial_rows=ts,outcome='failure')
            self.assertEqual(len(calls),2)
            self.assertFalse(result['dataset_eligible'])
            result,calls=self.run_fixture(root,station_rows=ss)
            self.assertEqual(calls,[])

    def test_calibration_only_advances_without_repeating_trials(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            result,calls=self.run_fixture(root,calibration_only=True,trial_rows=trials())
            self.assertEqual(result['status'],'calibration_complete'); self.assertFalse(result['dataset_eligible'])
            self.assertEqual(calls,[])
            result,calls=self.run_fixture(root)
            self.assertEqual(result['status'],'retained'); self.assertEqual(len(calls),1)

    def test_no_path_preserves_calibration_without_fake_episode(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            result,calls=self.run_fixture(root,trial_rows=trials(),path=False)
            self.assertEqual(result['status'],'no_successful_rollout'); self.assertEqual(calls,[])
            failure=read_json(root/'tasks/t/selection.json')['failed_rollouts'][0]
            self.assertEqual(failure['status'],'path_unavailable'); self.assertIsNone(failure['attempt'])

    def test_resume_retries_error_trial_and_preserves_old_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); ts=trials(); ts[0]['status']='episode_error'
            result,calls=self.run_fixture(root,trial_rows=ts,calibration_only=True)
            self.assertEqual(len(calls),1); self.assertEqual(result['status'],'calibration_complete')
            self.assertEqual(len(read_json(root/'tasks/t/trials.json')),11)

    def test_incomplete_trial_budget_not_formal_data(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_fixture(Path(d),max_trials=1)
            self.assertEqual(len(calls),1); self.assertEqual(result['status'],'incomplete')
            self.assertFalse(result['dataset_eligible'])


class PublicationTests(unittest.TestCase):
    def input_fixture(self, root):
        dataset=root/'dataset'; dataset.mkdir()
        asset=root/'robot.xml'; asset.write_text('SYNTHETIC asset identity')
        xml=root/'procthor-10k-val/val_1.xml'; xml.parent.mkdir(); xml.write_text('SYNTHETIC source identity')
        metadata=xml.with_name('val_1_metadata.json'); write_json(metadata,{'fixture':True})
        robot=asdict(RobotConfig(str(asset)))
        entries=[]; manifests={}
        for sample in ('sample-a','sample-b'):
            package=dataset/'samples'/sample/'robot_sensor_configs/rs-fixture'
            write_json(package/'scene/version.json',{'robot':robot,'source':{
                'xml_path':str(xml),'metadata_path':str(metadata),'xml_sha256':file_digest(xml),'metadata_sha256':file_digest(metadata)}})
            write_json(package/'task.json',{'task_id':'pick-'+sample,'operation':'pick'})
            (package/'scene/model.mjb').write_bytes(b'SYNTHETIC compiled model identity')
            entries.append({'sample_id':sample,'robot_sensor_config_id':'rs-fixture','path':str(package.relative_to(dataset))})
            manifests[str(package)]={'sample_id':sample,'robot_sensor_config_id':'rs-fixture','scene_dataset_id':'fixture-dataset',
                'task_id':'pick-'+sample,'case':'Case1','scene_id':'procthor-10k-val/val_1','station_count':20,
                'binding':{'station_geometry':{}},'package_identity':'fixture-sha',
                'scene_identity':{'model.mjb':file_digest(package/'scene/model.mjb')}}
        for package, manifest in manifests.items():
            write_json(Path(package) / 'manifest.json', manifest)
        write_json(dataset/'manifest.json',{'schema_version':'scene-datasets-research-v2','status':'failed','scene_dataset_id':'fixture-dataset','missing_samples':1})
        write_rows(dataset/'samples.jsonl',[{'sample_id': e['sample_id'], 'path': str(Path(e['path']).parents[1]),
            'robot_sensor_config_ids': [e['robot_sensor_config_id']]} for e in entries])
        return dataset,manifests

    def test_same_house_packages_get_distinct_units_and_partial_scope_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            dataset,manifests=self.input_fixture(Path(d))
            with patch('lastmile_dataflow.workflows.lastmile_datasets.package_manifest',side_effect=lambda p,*args:manifests[str(p)]):
                source,units=input_units(dataset,NoEditConfig())
                self.assertEqual(source['status'],'failed'); self.assertEqual([u['house'] for u in units],[1,1])
                self.assertNotEqual(units[0]['unit_id'],units[1]['unit_id'])
                self.assertTrue(all(u['config']['lastmile_selection'] for u in units))
                with self.assertRaisesRegex(ValueError,'subset'): input_units(dataset,NoEditConfig(),samples=['foreign'])

    def test_preflight_has_no_gpu_or_execution_and_wrong_schema_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); dataset,manifests=self.input_fixture(root)
            config=root/'config.json'; write_json(config,{'schema_version':'no-edit-v1'})
            with patch('lastmile_dataflow.workflows.lastmile_datasets.package_manifest',side_effect=lambda p,*args:manifests[str(p)]), \
                 patch('lastmile_dataflow.workflows.lastmile_datasets.collect_batch') as execute:
                output=collect(dataset,root,config,root/'outputs',run_id='fixture',gpu_ids=[0],preflight_only=True)
                execute.assert_not_called()
                summary=read_json(output/'summary.json')
                self.assertEqual(summary['selected_points'],40); self.assertTrue(summary['no_physical_execution'])
                write_json(dataset/'manifest.json',{'modified':True})
                with self.assertRaisesRegex(ValueError,'research scene dataset'): input_units(dataset,NoEditConfig())

    def test_success_but_incomplete_recording_is_not_training_data(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); parent=root/'research'; parent.mkdir()
            child=root/'child'; taskroot=child/'scenes/val_1/tasks/t'; taskroot.mkdir(parents=True)
            attempt=child/'scenes/val_1/attempts/pick/low/synthetic'; attempt.mkdir(parents=True)
            write_json(attempt/'attempt.json', {'attempt_id':'synthetic'})
            write_json(attempt/'config.json', {})
            record={'config_sha256':digest({}), 'schema_version':'research-episode-v1', 'status':'complete', 'episode_id':'synthetic',
                'task_id':'fixture', 'role':'recovery', 'trial_id':'pair', 'termination_status':'success',
                'compiled_model_sha256':'fixture', 'execution_status':'executed', 'required_files':[],
                'task_success':True, 'simulation_valid':True, 'recording_complete':False, 'data_valid':False,
                'navigation_success':True, 'continuous_simulation':True, 's0':stations()[0], 's1':stations()[1]}
            write_json(attempt/'episode.json',record)
            write_json(taskroot/'task.json',{'task_id':'fixture','operation':'pick'})
            write_json(taskroot/'result.json',{'status':'retained','dataset_eligible':True})
            write_json(taskroot/'selection.json',{'start_groups':[{'s0':'low','s1_selected':['high']}]})
            unit={'run_path':str(child),'house':1,'unit_id':'u','selected_points':2, **dict.fromkeys(
                  ['scene_dataset_id','sample_id','robot_sensor_config_id','task_id','compiled_model_sha256','source_package_identity'],'fixture')}
            view=collect_views(parent,[unit],export_state_images=False)
            self.assertEqual(view['all_attempts'],1); self.assertEqual(view['recovery_attempts'],1)
            self.assertEqual(view['train_episodes'],0); self.assertEqual(view['successful_recoveries'],1)
            self.assertFalse(view['unit_results'][0]['start_groups'][0]['minimum_met'])
            write_json(attempt/'episode.json',{**record,'recording_complete':True,'data_valid':True})
            view=collect_views(parent,[unit],export_state_images=False)
            self.assertEqual(view['train_episodes'],1)
            self.assertEqual(view['unit_results'][0]['start_groups'][0]['warnings'],['single_successful_solution'])
            self.assertTrue((parent/'episodes.jsonl').is_file())
            for name in ('all_attempts_index.jsonl','lastmile_index.jsonl','train_index.jsonl','exports.jsonl'):
                self.assertFalse((parent/name).exists())

    def test_phase_ranges_follow_actual_interval_times(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write_rows(root/'trajectory.jsonl',[{'observations':{'phase':p},'command':{
                'time_before_s':i*.1,'time_after_s':(i+1)*.1}} for i,p in enumerate(['navigation','navigation','arrival','hold'])])
            phases=phase_ranges(root)
            self.assertEqual([p['phase'] for p in phases],['navigation','arrival','hold'])
            self.assertEqual(phases[0]['last_frame'],2); self.assertAlmostEqual(phases[0]['t_end'],.2)

    def test_parent_resume_identity_changes_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); config=root/'config.json'; write_json(config,{'schema_version':'no-edit-v1'})
            units=[{'unit_id':'u','sample_id':'s','robot_sensor_config_id':'rs','selected_points':2}]
            dataset=root/'dataset'; write_json(dataset/'manifest.json',{'fixture':True})
            with patch('lastmile_dataflow.workflows.lastmile_datasets.input_units',return_value=({'status':'sealed'},units)):
                collect(dataset,root,config,root/'outputs',run_id='fixture',gpu_ids=[0],preflight_only=True)
                with self.assertRaisesRegex(ValueError,'mismatch'):
                    collect(dataset,root,config,root/'outputs',run_id='fixture',gpu_ids=[0],preflight_only=True,resume=True,max_trials=1)

    def test_resume_ignores_code_export_and_resource_diagnostics_not_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); dataset, manifests=self.input_fixture(root)
            config=root/'config.json'; write_json(config, {'schema_version':'no-edit-v1'})
            with patch('lastmile_dataflow.workflows.lastmile_datasets.package_manifest', side_effect=lambda p,*args:manifests[str(p)]):
                collect(dataset,root,config,root/'outputs',run_id='resume',gpu_ids=[0],preflight_only=True)
                write_json(config, {'schema_version':'no-edit-v1', 'min_free_disk_gb':99, 'third_person_enabled':False})
                with patch('lastmile_dataflow.workflows.lastmile_datasets.code_identity',return_value={'code_commit':'different'}):
                    collect(dataset,root,config,root/'outputs',run_id='resume',gpu_ids=[0],preflight_only=True,
                            resume=True,export_state_images=False)
                write_json(config, {'schema_version':'no-edit-v1', 'attempt_timeout_s':9999})
                with self.assertRaisesRegex(ValueError,'critical input/config mismatch'):
                    collect(dataset,root,config,root/'outputs',run_id='resume',gpu_ids=[0],preflight_only=True,resume=True)

    def test_v0_serial_workers_explicit_and_invalid_subsets(self):
        with self.assertRaisesRegex(ValueError,'serially'):
            collect('none','none','none','none',run_id='x',gpu_ids=[0],workers=2)


class NavigationTests(unittest.TestCase):
    def test_deep_audit_cli_is_not_a_normal_entry(self):
        from lastmile_dataflow.cli import parser
        commands = parser()._subparsers._group_actions[0].choices
        for name in ('audit-canonical', 'audit-scene-datasets', 'review-lastmile-timeouts'):
            self.assertNotIn(name, commands)
        self.assertIn('index-lastmile-datasets', commands)

    def test_arrival_drift_cannot_be_reported_as_success(self):
        config=NoEditConfig()
        state=np.zeros(3)
        robot=SimpleNamespace(config=RobotConfig('fixture'), group=lambda group:state.copy() if group=='base' else np.zeros(7),
                              neutral_action=lambda:np.zeros(20))
        ctx=SimpleNamespace(sim=SimpleNamespace(robot=robot),config=config,yaw_gap=lambda a,b:a-b)
        calls=[]
        def tick(action):
            calls.append(action)
            if len(calls)==10: state[0]=.05  # physically measured late drift in arrival hold
        ctx.tick=tick
        with self.assertRaisesRegex(OperationFailure,'arrival_tracking_error'):
            navigate(ctx,{'xy':[[0.,0.]]},{'base':[0.,0.,0.]})


if __name__ == '__main__':
    unittest.main()
