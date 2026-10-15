"""Real simplified MuJoCo stepping; mocked sensors/writers are not GPU evidence."""

from dataclasses import asdict
from itertools import count
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from helpers import make_sim
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.execution.context import ContinuousContext
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.workflows.collection import _station_trials


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sim, _ = make_sim(self.root)
        self.addCleanup(self.sim.close)

    def recorder(self, **options):
        collection = CollectionConfig(width=32, height=32, **options)
        recorder = AttemptRecorder(self.root, {'collection': asdict(collection)}, attempt_id='test')
        recorder.bind_simulation(self.sim)
        self.addCleanup(recorder.abort_close)
        return recorder

    def test_one_capture_per_boundary_and_phase_before_dispatch(self):
        recorder = self.recorder(record_images=True, record_video=True)
        context = ContinuousContext(self.sim, recorder, ExecutionConfig(width=32, height=32))
        self.addCleanup(context.finish)
        sensors = {
            name: {'rgb': np.zeros((32, 32, 3), dtype=np.uint8)} for name in self.sim.robot.camera_names
        }
        writers = [Mock() for _ in sensors]
        with (
            patch(
                'lastmile_dataflow.data.rendering.camera_calibration.capture_sensors', return_value=sensors
            ) as capture,
            patch('lastmile_dataflow.data.recorder.imageio.get_writer', side_effect=writers),
        ):
            for phase in ('navigation', 'operation'):
                context.phase = phase
                context.tick(self.sim.robot.neutral_action())
            recorder.finish('failure', 'fixture')
        self.assertEqual(capture.call_count, 3)  # Initial state + two actual intervals.
        self.assertEqual(len(list(recorder.path.glob('images/*/*.png'))), 3 * len(sensors))
        self.assertTrue(all(writer.append_data.call_count == 3 for writer in writers))
        self.assertTrue(all(writer.close.call_count == 1 for writer in writers))
        with np.load(recorder.path / 'trajectory.npz') as trajectory:
            self.assertEqual(trajectory['phases'].tolist(), ['navigation', 'operation'])
            self.assertEqual(len(trajectory['time_s']), 3)
            self.assertEqual(trajectory['actions'].shape, (2, 20))
        self.assertTrue(recorder.result['recording_complete'])

    def test_partial_physics_interval_is_recorded_once(self):
        recorder = self.recorder(record_images=False, record_video=False)
        self.sim.begin()
        checks = 0

        def check():
            nonlocal checks
            checks += 1
            if checks == 2:
                raise RuntimeError('interrupted physics interval')
            return {'valid': True}

        with self.assertRaisesRegex(RuntimeError, 'interrupted physics'):
            self.sim.step(self.sim.robot.neutral_action(), substep_check=check)
        recorder.finish('failure', 'fixture')
        with np.load(recorder.path / 'trajectory.npz') as trajectory:
            self.assertEqual(trajectory['actions'].shape, (1, 20))
            self.assertEqual(len(trajectory['time_s']), 2)
            self.assertAlmostEqual(trajectory['time_s'][-1], 0.008)
        self.assertTrue(recorder.result['recording_complete'])

    def test_attempt_counter_is_shared_across_scenes_without_file_scans(self):
        ids = count()
        config = ExecutionConfig(trials_per_station=1)
        result = {'status': 'failure', 'reason': 'fixture', 'recording_complete': True}
        with (
            patch('lastmile_dataflow.workflows.collection.run_curobo_attempt', return_value=result) as run,
            patch('lastmile_dataflow.workflows.collection.grasp_candidates', return_value=[]),
            patch('lastmile_dataflow.workflows.collection.rows', side_effect=AssertionError('no scans')),
        ):
            for scene_id in ('one', 'two'):
                scene = {
                    'scene_id': scene_id,
                    'task': {'task_id': 'pick'},
                    'stations': [{'station_id': 's0'}, {'station_id': 's1'}],
                }
                _station_trials(None, scene, self.root, config, None, self.root, attempt_ids=ids)
        self.assertEqual(
            [call.kwargs['attempt_id'] for call in run.call_args_list],
            ['r000000', 'r000001', 'r000002', 'r000003'],
        )
