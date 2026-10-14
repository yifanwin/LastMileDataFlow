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
from lastmile_dataflow.planning.astar import Grid
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.data.statistics import aggregate, is_episode_terminal
from lastmile_dataflow.runtime.episode_guard import execute_isolated_episode, track_resource, mark_episode_phase, classify_episode_exception
from lastmile_dataflow.policies.molmobot import MolmoBotClient, PolicyInfrastructureError, PolicyEpisodeError
from lastmile_dataflow.data.recorder import AttemptRecorder


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
            self.assertIn('injected',result['error'])
            self.assertFalse(Path(result['attempt']).exists())


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




    def test_interruption_not_swallowed_and_resources_closed(self):
        resource=io.StringIO()
        def run(root,task,station,attempt_id):track_resource(resource);raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.execute(run)
        self.assertTrue(resource.closed)

    def test_real_synthetic_mjdata_initialization_failure_closes_clone_not_baseline(self):
        from helpers import make_sim
        from lastmile_dataflow.runtime.execution import run_curobo_attempt, clone_sim
        from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
        baseline,robot=make_sim(self.root)
        self.addCleanup(baseline.close);before=baseline.state_vector().copy();clones=[]
        def clone(*args,**kwargs):
            probe=clone_sim(*args,**kwargs);clones.append(probe);return probe
        task={**self.task,'target_body':'target'}
        for backend in ('curobo','molmobot'):
            module='lastmile_dataflow.runtime.'+('execution' if backend=='curobo' else 'molmobot_execution')
            with patch(module+'.clone_sim',side_effect=clone), \
                 patch(module+'.place_frozen_station',side_effect=ValueError('bad initial pose')):
                if backend=='curobo':
                    result=run_curobo_attempt(baseline,task,self.station,[],self.root,ExecutionConfig(),CollectionConfig(output_dir=str(self.root)),self.root,backend,seed=1)
                else:
                    result=run_vla_attempt(baseline,task,self.station,ExecutionConfig(),CollectionConfig(output_dir=str(self.root)),self.root,backend,seed=1,policy=object())
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
