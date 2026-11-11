from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.data.rendering.rl_capture import RolloutCapture


class VideoSelectionTests(unittest.TestCase):
    def test_success_with_collision_shares_one_representative_video(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'views').mkdir()
            env = SimpleNamespace(task=SimpleNamespace(stations=[{'station_id':'S1','split':'validation'}]),
                                  sim=SimpleNamespace(mj_model=None),step_dt=.05,
                                  prepared=SimpleNamespace(target_body='cup'))
            capture = RolloutCapture(output,100,{'video':{}},representatives=True)
            capture.stations,capture.seed,capture.mode = [0],42,'stochastic'
            capture.states,capture.metrics = [[np.zeros(2)]],[['measured facts']]
            row = {'success':True,'collision_free':False,'infrastructure_failure':False,'failed':False}
            with patch('lastmile_dataflow.data.rendering.rl_capture.render_rollout',return_value={'path':'video.mp4'}) as render:
                capture.finish(env,[row])
                capture.finish(env,[row])
            self.assertEqual(render.call_count,1)
            self.assertEqual(env._representative_video_categories,{'success','collision'})
            self.assertEqual(capture.videos[0]['categories'],['collision','success'])
