"""Stage scripts: device binding, run-config dispatch and runnable --help."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lastmile_dataflow.config import REPO_ROOT, StationRun, load_station_run
from lastmile_dataflow.gpu import select_devices
from lastmile_dataflow.workflows import collection

SCRIPTS = ('build_scenes', 'station_map', 'collect', 'bench_build', 'bench_evaluate')


class StageScriptTests(unittest.TestCase):
    def test_every_stage_script_starts_outside_the_repository(self):
        for name in SCRIPTS:
            result = subprocess.run([sys.executable, str(REPO_ROOT / 'scripts' / f'{name}.py'), '--help'],
                                    cwd=tempfile.gettempdir(), capture_output=True, text=True, check=True)
            self.assertIn('--output', result.stdout, name)

    def test_select_devices_binds_cuda_and_egl_to_the_first_card(self):
        with patch.dict(os.environ, {}, clear=True):
            select_devices([3, 5])
            self.assertEqual((os.environ['CUDA_VISIBLE_DEVICES'], os.environ['MUJOCO_EGL_DEVICE_ID']), ('3', '3'))
            select_devices([3], [1])
            self.assertEqual(os.environ['MUJOCO_EGL_DEVICE_ID'], '1')
            self.assertEqual(os.environ['MUJOCO_GL'], 'egl')

    def test_station_run_defaults_match_the_former_cli_flags(self):
        run = load_station_run('configs/collection/panda_omron_robocasa_curobo.json')
        self.assertEqual(run.execution, str(REPO_ROOT / 'configs/execution/panda_omron_robocasa_curobo.json'))
        self.assertEqual((run.station_workers, run.planning_workers_per_gpu, run.cpu_workers,
                          run.preparation_workers, run.record_video), (1, [1], 60, 12, False))
        with self.assertRaises(ValueError):
            StationRun(execution='x', recording_mode='video')

    def test_run_stations_dispatches_recording_and_resources(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, 'run.json')
            path.write_text(json.dumps({'execution': 'configs/execution/panda_omron_robocasa_curobo.json',
                                        'assets_dir': root, 'record_video': True, 'station_workers': 2}))
            with patch.object(collection, 'evaluate_stations', return_value='cal') as evaluate, \
                    patch.object(collection, 'collect_lastmile', return_value='col') as collect:
                self.assertEqual(collection.run_stations('scenes', path, Path(root, 'runs/cal'), gpu_ids=[0],
                                                         collect=False), 'cal')
                self.assertEqual(collection.run_stations('scenes', path, Path(root, 'runs/col'), gpu_ids=[0],
                                                         collect=True, calibration='cal'), 'col')
        args, options = evaluate.call_args
        self.assertEqual((args[0], args[1], args[4]), ('scenes', root, Path(root, 'runs')))
        self.assertEqual((options['run_id'], options['station_workers'], options['recording_mode']),
                         ('cal', 2, 'visual'))
        # Shared Panda preparation records images only when collecting, as the CLI did.
        self.assertFalse(args[3].record_images)
        self.assertTrue(args[3].record_video)
        self.assertTrue(collect.call_args[0][3].record_images)
        self.assertEqual(collect.call_args[1]['calibration'], 'cal')
