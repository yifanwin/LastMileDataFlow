"""Panda semantic recording and preparation snapshots, without external assets."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.control.interface import RobotCommand
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.execution.context import clone_sim
from lastmile_dataflow.scenes.loading.source import SceneSource
from tests.runtime.test_robot_interface import panda_adapter


class PandaRecordingTests(unittest.TestCase):
    def test_semantic_targets_snapshot_and_recorded_boundary_alignment(self):
        robot = panda_adapter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sim = Simulation(robot.model, robot.config, [])
            sim.robot.initialize()
            sim.source = SceneSource('test', 'unused.xml')
            sim.restoration = None
            try:
                sim.freeze(root/'scene')
                restored = Simulation.from_snapshot(root/'scene', robot.config)
                try:
                    self.assertEqual(restored.robot.state_targets(), sim.robot.state_targets())
                    cloned = clone_sim(restored)
                    try:
                        self.assertEqual(cloned.robot.state_targets(), sim.robot.state_targets())
                    finally:
                        cloned.close()
                finally:
                    restored.close()
                collection = CollectionConfig(record_images=False, record_video=False)
                recorder = AttemptRecorder(root, {'collection':asdict(collection)}, attempt_id='semantic')
                recorder.bind_simulation(sim)
                sim.begin()
                sim.step(RobotCommand(gripper_width={'right':.04}))
                recorder.finish('failure', 'protocol_check')
                with np.load(recorder.path/'trajectory.npz', allow_pickle=False) as trace:
                    self.assertEqual(trace['actions'].shape, (1, len(sim.robot.record_layout()['action_fields'])))
                    self.assertEqual(trace['state'].shape[0], 2)
                    self.assertEqual(len(trace['time_s']), 2)
                    self.assertAlmostEqual(trace['actions'][0,-1], .04)
                    self.assertAlmostEqual(trace['requested_actions'][0,-1], .04)
                    self.assertGreater(trace['time_s'][1], trace['time_s'][0])
                    self.assertNotIn('state22', trace)
            finally:
                sim.close()

    def test_materialization_reads_panda_fields_without_rby_state(self):
        from lastmile_dataflow.data.export.lastmile_dataset import materialize_lastmile, load_training_batch
        from lastmile_dataflow.utils.io import write_json, write_rows
        layout = panda_adapter().record_layout()
        size = len(layout['state_fields'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'reference'
            rollout = source/'rollouts'/'fixture'
            rollout.mkdir(parents=True)
            config = {'config':{},'collection':{'record_images':False,'record_depth':False},
                      'profiles':{'fixture':{'robot':{'name':'panda_omron','cameras':[]}}}}
            write_json(source/'config.json',config)
            write_json(rollout/'recording.json',{'complete':True,'steps':2,'layout':layout,'task':{'instruction':'software fixture'}})
            np.savez_compressed(rollout/'trajectory.npz',state=np.zeros((3,size)),actions=np.ones((2,size)),
                requested_actions=np.ones((2,size)),time_s=np.arange(3)*.05,phases=np.array(['navigation','hold']))
            record = {'scene_id':'fixture','source_run':'.','source_type':'s1_direct_operation',
                      'status':'success','task_outcome':{'status':'success'},'recording_complete':True,
                      'executed_steps':2,'rollout_id':'fixture'}
            write_rows(source/'train_index.jsonl',[record])
            write_rows(source/'pair_index.jsonl',[])
            output = materialize_lastmile(source,root/'materialized')
            batch = load_training_batch(output,[0],steps=2)
            self.assertEqual(batch['state'].shape,(1,2,size))
            self.assertEqual(batch['actions'].shape,(1,2,size))
            self.assertNotIn('state22',batch)
