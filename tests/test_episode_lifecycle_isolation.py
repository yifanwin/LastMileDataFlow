"""Fault-injection regressions; fixtures are not real GPU/VLA acceptance."""
from pathlib import Path
import errno
import io
import tempfile
import unittest
from unittest.mock import patch, Mock
from contextlib import ExitStack
import numpy as np

from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.navigation.astar import Grid
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.stations.no_edit_statistics import aggregate,construction_rate,is_episode_terminal
from lastmile_dataflow.runtime.episode_guard import (execute_isolated_episode,track_resource,mark_episode_phase,
    classify_episode_exception,isolated_recording)
from lastmile_dataflow.policies.molmobot import MolmoBotClient,PolicyInfrastructureError,PolicyEpisodeError
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.workflows.no_edit import collect_task


class LifecycleIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.task={'operation':'pick','task_id':'t'}
        self.station={'station_id':'S0001','xy':[0,0],'geometry':'valid'}

    def execute(self,fn,attempt='test'):
        return execute_isolated_episode(fn,self.root,self.task,self.station,attempt)

    def test_phase_errors_are_local_and_resources_are_closed(self):
        for phase,status in [('initialize','initialization_error'),('execution','episode_error'),('finalize','recording_error')]:
            resource=io.StringIO()
            def run(root,task,station,attempt_id):
                track_resource(resource);mark_episode_phase(phase);raise ValueError('injected '+phase)
            result=self.execute(run,phase)
            self.assertEqual(result['status'],status);self.assertTrue(resource.closed)
            self.assertFalse(result['artifact_complete']);self.assertIsNone(result['executed_steps'])
            self.assertIn('injected',read_json(result['error_evidence'])['traceback'])
            self.assertFalse(Path(result['attempt']).exists())

    def test_partial_recorder_constructor_cleans_open_streams(self):
        created=[]
        original_open=Path.open
        def fail_events(path,*args,**kwargs):
            if path.name=='events.jsonl':raise ValueError('event writer init failure')
            f=original_open(path,*args,**kwargs)
            if path.name=='trajectory.jsonl':created.append(f)
            return f
        def run(root,task,station,attempt_id):
            AttemptRecorder(root,{'collection':{'record_video':False}},attempt_id=attempt_id)
        with patch.object(Path,'open',fail_events):result=self.execute(run)
        self.assertEqual(result['status'],'initialization_error')
        self.assertTrue(created);self.assertTrue(all(f.closed for f in created))

    def test_generic_runtime_error_local_but_gpu_rpc_storage_are_shared(self):
        for exc in [ValueError('bad station'),KeyError('target'),RuntimeError('bad episode geometry')]:
            self.assertEqual(classify_episode_exception(exc)['failure_scope'],'episode')
        for exc in [PolicyInfrastructureError('model response timeout'),RuntimeError('CUDA out of memory'),
                    MemoryError('allocation failed'),OSError(errno.ENOSPC,'disk full'),OSError(errno.EIO,'I/O failed')]:
            self.assertEqual(classify_episode_exception(exc)['failure_scope'],'shared')

    def test_model_reply_consumed_data_error_local_connection_error_shared(self):
        client=MolmoBotClient.__new__(MolmoBotClient);client.connection=Mock()
        client.connection.poll.return_value=True
        for kind in ['ValueError','RuntimeError','KeyError']:
            client.connection.recv.return_value={'error':'bad observation','type':kind}
            with self.assertRaises(PolicyEpisodeError):client.receive(1)
        client.connection.recv.return_value={'error':'CUDA out of memory','type':'RuntimeError'}
        with self.assertRaises(PolicyInfrastructureError):client.receive(1)
        client.connection.poll.return_value=False
        with self.assertRaises(PolicyInfrastructureError):client.receive(1)

    def test_video_finalize_local_failure_not_global_and_committed_evidence_not_rewritten(self):
        for error,expected in [(RuntimeError('encoder failed'),'recording_error'),
                               (OSError(errno.ENOSPC,'disk full'),'infrastructure_error')]:
            def run(root,task,station,attempt_id):
                recorder=AttemptRecorder(root,{'collection':{'record_video':False}},attempt_id=attempt_id)
                recorder.writers['head']=Mock(close=Mock(side_effect=error))
                recorder.finish('success','fixture',{'task_completion':{'status':'unknown'}})
                return {'status':'infrastructure_error','reason':'video_finalize_failed','station_id':station['station_id'],
                        'attempt':str(recorder.path),'executed_steps':0}
            result=self.execute(run,expected)
            self.assertEqual(result['status'],expected)
            self.assertEqual(read_json(Path(result['attempt'])/'result.json')['status'],'infrastructure_error')
            self.assertTrue((Path(result['attempt'])/'episode.json').exists())

    def test_recording_diagnostic_local_continue_storage_fail_stop(self):
        self.assertIsNone(isolated_recording(self.root,'one','heatmap',Mock(side_effect=ValueError('plot failed'))))
        with self.assertRaises(OSError):
            isolated_recording(self.root,'two','trials',Mock(side_effect=OSError(errno.ENOSPC,'disk full')))
        self.assertEqual(len(list((self.root/'episode_errors').glob('*.json'))),2)

    def test_unknown_errors_not_counted_as_physical_failures(self):
        records=[{'station_id':'S0001','trial':i,'status':status,'attempt':str(i)} for i,status in enumerate(
            ['initialization_error','episode_error','recording_error','simulation_failure','success'])]
        self.assertTrue(all(is_episode_terminal(r) for r in records))
        stats=aggregate([self.station],records,5)[0]
        self.assertEqual((stats['completed_episodes'],stats['terminal_trials'],stats['episode_errors']),(5,1,4))
        self.assertEqual(stats['success_rate'],1.)
        self.assertEqual(construction_rate([stats],5,.05)['status'],'completed_with_episode_errors')
        all_errors=aggregate([self.station],records[:4],4)[0]
        self.assertIsNone(all_errors['success_rate'])

    def test_interruption_not_swallowed_and_resources_closed(self):
        resource=io.StringIO()
        def run(root,task,station,attempt_id):track_resource(resource);raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.execute(run)
        self.assertTrue(resource.closed)

    def test_unserializable_result_is_isolated_before_shared_ledger(self):
        def run(root,task,station,attempt_id):
            mark_episode_phase('execution')
            return {'station_id':station['station_id'],'status':'success','bad':object()}
        self.assertEqual(self.execute(run)['status'],'episode_error')

    def test_both_collectors_continue_and_resume_skip_local_errors(self):
        for backend in ('curobo','molmobot'):
            for failure in [ValueError('station failed'),PolicyInfrastructureError('connection lost')]:
                with self.subTest(backend=backend,error=type(failure).__name__), tempfile.TemporaryDirectory() as d:
                    root=Path(d)/'val_0';folder=root/'tasks/t';folder.mkdir(parents=True)
                    write_json(root/'robot_geometry.json',{'radius_m':.4})
                    stations=[{'station_id':k,'xy':xy,'geometry':'valid'} for k,xy in [('a',[0,0]),('b',[0,1])]]
                    write_json(folder/'stations.json',stations)
                    task={'operation':'pick','task_id':'t','output_name':'t','target_body':'target','anchor_world':[0,0,1],'instruction':'fixture'}
                    config=NoEditConfig() if backend=='curobo' else NoEditConfig(operation_backend='molmobot',policy_config='fixture',operation_base_mode='policy_relative',width=1024,height=576)
                    calls=[]
                    def execute(*args,**kwargs):
                        calls.append(args[2]['station_id'])
                        if len(calls)==1:raise failure
                        return {'status':'failure','station_id':args[2]['station_id'],'attempt':'fixture','attribution':{}}
                    name='lastmile_dataflow.runtime.'+('molmobot_execution.run_vla_attempt' if backend=='molmobot' else 'no_edit_execution.run_raw_attempt')
                    grid=Grid(np.array([-1.,-1.]),1.,np.ones((6,6),bool))
                    with ExitStack() as stack:
                        for target,kw in [
                            ('lastmile_dataflow.tasks.raw_scene.grasp_candidates',{'return_value':[]}),
                            ('lastmile_dataflow.navigation.astar.task_support_obstacles',{'return_value':[]}),
                            ('lastmile_dataflow.navigation.astar.scene_grid',{'return_value':grid}),
                            ('lastmile_dataflow.exporting.no_edit_heatmap.export_heatmap',{'side_effect':ValueError('bad plot')}),
                            ('lastmile_dataflow.exporting.navigation_map.export_navigation_map',{}),
                            ('lastmile_dataflow.workflows.no_edit.check_disk',{}),(name,{'side_effect':execute})]:
                            stack.enter_context(patch(target,**kw))
                        result=collect_task(None,task,d,config,CollectionConfig(output_dir=d),root,policy=object())
                        if isinstance(failure,PolicyInfrastructureError):
                            self.assertEqual(len(calls),1);self.assertEqual(result['collection_error'],'infrastructure_error')
                        else:
                            self.assertEqual(calls,['a']*5+['b']*5)
                            self.assertEqual(result['completed_episodes'],10)
                            self.assertEqual(result['episode_errors'],1)
                            self.assertIsNone(result['collection_error'])
                            self.assertFalse(result['recording_complete'])
                            self.assertTrue(result['recording_errors'])
                            write_json(folder/'result.json',{**result,'status':'incomplete'})
                            collect_task(None,task,d,config,CollectionConfig(output_dir=d),root,policy=object())
                            self.assertEqual(len(calls),10)

    def test_real_synthetic_mjdata_initialization_failure_closes_clone_not_baseline(self):
        from helpers import make_sim
        from lastmile_dataflow.runtime.no_edit_execution import run_raw_attempt,clone_sim
        from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
        baseline,robot=make_sim(self.root)
        self.addCleanup(baseline.close);before=baseline.state_vector().copy();clones=[]
        def clone(*args,**kwargs):
            probe=clone_sim(*args,**kwargs);clones.append(probe);return probe
        task={**self.task,'target_body':'target'}
        for backend in ('curobo','molmobot'):
            module='lastmile_dataflow.runtime.'+('no_edit_execution' if backend=='curobo' else 'molmobot_execution')
            with patch(module+'.clone_sim',side_effect=clone), \
                 patch(module+'.place_frozen_station',side_effect=ValueError('bad initial pose')):
                if backend=='curobo':
                    result=run_raw_attempt(baseline,task,self.station,[],self.root,NoEditConfig(),CollectionConfig(output_dir=str(self.root)),self.root,backend,seed=1)
                else:
                    result=run_vla_attempt(baseline,task,self.station,NoEditConfig(),CollectionConfig(output_dir=str(self.root)),self.root,backend,seed=1,policy=object())
            self.assertEqual(result['status'],'initialization_error')
            self.assertTrue(clones[-1].closed)
            self.assertFalse(baseline.closed)
            np.testing.assert_array_equal(before,baseline.state_vector())

    def test_mid_execution_recorder_failure_classifies_as_recording_error(self):
        def run(root,task,station,attempt_id):
            recorder=AttemptRecorder(root,{'collection':{'record_video':False}},attempt_id=attempt_id)
            mark_episode_phase('execution')
            recorder.event('bad-event',bad=object())
        result=self.execute(run,'bad-recording')
        self.assertEqual(result['status'],'recording_error')
        self.assertEqual(result['attribution']['phase'],'recording')
        self.assertFalse(result['execution_started'])

    def test_poll_connection_failure_is_shared(self):
        client=MolmoBotClient.__new__(MolmoBotClient);client.connection=Mock()
        client.connection.poll.side_effect=OSError(errno.EPIPE,'closed channel')
        with self.assertRaises(PolicyInfrastructureError):client.receive(1)

    def test_parent_index_skips_recording_gaps_and_isolates_bad_export(self):
        from lastmile_dataflow.workflows.no_edit import rebuild_dataset_index
        self.root.mkdir(exist_ok=True)
        task=self.root/'scenes/val_0/tasks/t'
        write_json(task/'result.json',{'recording_complete':False,'dataset_eligible':True})
        self.assertEqual(rebuild_dataset_index(self.root),0) # no task/selection files required for known gap
        write_json(task/'result.json',{'recording_complete':True,'dataset_eligible':True})
        write_json(task/'task.json',{'instruction':'synthetic','task_id':'t','operation':'pick'})
        write_json(task/'selection.json',{'successful':[{'status':'success','navigation':{'status':'success'},'attempt':'synthetic'}]})
        with patch('lastmile_dataflow.workflows.no_edit.export_rollout',side_effect=ValueError('bad media manifest')):
            self.assertEqual(rebuild_dataset_index(self.root),0)
        self.assertTrue(list((self.root/'scenes/val_0/episode_errors').glob('*dataset_export*')))
