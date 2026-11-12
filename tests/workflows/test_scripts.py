"""Stage scripts: device binding, run-config dispatch and runnable --help."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lastmile_dataflow.config import REPO_ROOT, StationRun, load_station_run
from lastmile_dataflow.gpu import select_devices

SCRIPTS = ('build_scenes', 'station_map', 'collect', 'bench_build', 'bench_evaluate')


class StageScriptTests(unittest.TestCase):
    def test_every_stage_script_starts_outside_the_repository(self):
        for name in SCRIPTS:
            result = subprocess.run([sys.executable, str(REPO_ROOT / 'scripts' / f'{name}.py'), '--help'],
                                    cwd=tempfile.gettempdir(), capture_output=True, text=True, check=True)
            self.assertIn('--output', result.stdout, name)

    def test_select_devices_binds_cuda_and_egl_to_the_first_card(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch('lastmile_dataflow.gpu.egl_device_map', return_value={3: 4, 5: 2}):
            select_devices([3, 5])
            self.assertEqual((os.environ['CUDA_VISIBLE_DEVICES'], os.environ['MUJOCO_EGL_DEVICE_ID']), ('3', '4'))
            select_devices([3], [1])
            self.assertEqual(os.environ['MUJOCO_EGL_DEVICE_ID'], '1')
            self.assertEqual(os.environ['MUJOCO_GL'], 'egl')

    def test_station_run_defaults_match_the_former_cli_flags(self):
        run = load_station_run('configs/collection/panda_omron_robocasa_curobo.json')
        self.assertEqual(run.execution, str(REPO_ROOT / 'configs/execution/panda_omron_robocasa_curobo.json'))
        self.assertEqual((run.planning_workers_per_gpu, run.cpu_workers, run.preparation_workers, run.record_video,
                          run.mode), ([1], 60, 12, False, 'numerical'))
        with self.assertRaises(ValueError):
            StationRun(execution='x', recording_mode='video')

