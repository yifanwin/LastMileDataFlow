"""三相机 + 俯视轨迹组合帧的纯布局检查；不加载 MuJoCo。"""

import unittest

import numpy as np

from lastmile_dataflow.data.rendering.rollout_video import (
    HEADER, FOOTER, TILE, _nav_panel, compose_grid,
)


def frames():
    return {
        'robot0_agentview_left': np.full((TILE, TILE, 3), 30, np.uint8),
        'robot0_agentview_right': np.full((TILE, TILE, 3), 80, np.uint8),
        'robot0_eye_in_hand': np.full((TILE, TILE, 3), 140, np.uint8),
    }


class GridVideoTests(unittest.TestCase):
    def test_grid_keeps_three_cameras_and_top_down_panel(self):
        image = compose_grid(
            frames(),
            instruction='fixture pick',
            station_id='S0001',
            phase='approach',
            time_s=1.25,
            step=25,
            steps=100,
            base_history=np.asarray([[0., 0., 0.], [.2, .3, .4]]),
            station_xy=np.asarray([.2, .3]),
            anchor_xy=np.asarray([.8, .1]),
            supports=[{'min': [.5, -.2, 0.], 'max': [1.1, .4, .7]}],
            base_radius_m=.438,
        )
        self.assertEqual(image.shape, (HEADER + 2 * TILE + FOOTER, 2 * TILE, 3))
        top = image[HEADER:HEADER + TILE]
        bottom = image[HEADER + TILE:HEADER + 2 * TILE]
        self.assertEqual(int(top[24:TILE, :TILE].mean()), 30)
        self.assertEqual(int(top[24:TILE, TILE:].mean()), 80)
        self.assertEqual(int(bottom[24:TILE, :TILE].mean()), 140)
        self.assertGreater(float(bottom[:TILE, TILE:].std()), 1.0)

    def test_top_down_panel_draws_target_station_and_trail(self):
        panel = _nav_panel(
            np.asarray([[0., 0., 0.], [.4, .2, .5]]),
            station_xy=np.asarray([0., 0.]),
            anchor_xy=np.asarray([.9, .5]),
            supports=[],
            radius_m=.438,
        )
        self.assertEqual(panel.shape, (TILE, TILE, 3))
        self.assertGreater(float(panel.std()), 0.0)
        colors = {tuple(c) for c in panel.reshape(-1, 3)[::97]}
        self.assertTrue(any(c[1] > c[0] for c in colors))
        self.assertTrue(any(c[0] > 200 and c[1] > 150 for c in colors))


if __name__ == '__main__':
    unittest.main()
