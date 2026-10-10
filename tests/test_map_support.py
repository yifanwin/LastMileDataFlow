"""Small geometry/visual regression fixtures, not real navigation evidence."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import mujoco
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.navigation.astar import Grid,scene_grid,search,task_support_obstacles,distance_field
from lastmile_dataflow.exporting.no_edit_heatmap import gaussian_map,export_heatmap
from lastmile_dataflow.recording.analysis_video import AnalysisVideo
from lastmile_dataflow.exporting.navigation_map import export_navigation_map


class SupportMapTests(unittest.TestCase):
    def test_original_geodesic_kernel_with_only_support_radius_changed(self):
        free=np.ones((41,41),bool);free[:25,23]=False
        grid=Grid(np.zeros(2),.05,free)
        rows=[{'geometry':'valid','terminal_trials':2,'successes':1,'xy':[1.02,1.02]},
              {'geometry':'valid','terminal_trials':3,'successes':3,'xy':[.9,1.1]}]
        values,counts=gaussian_map(grid,rows,.44)
        numerator=np.zeros(free.shape);denominator=np.zeros(free.shape)
        for row in rows:
            dist=distance_field(grid,row['xy'],.35)
            weight=np.exp(-.5*(dist/.44)**2)
            numerator+=weight*row['successes'];denominator+=weight*row['terminal_trials']
        expected=np.full(free.shape,np.nan)
        observed=free & (denominator>1e-12)
        expected[observed]=numerator[observed]/denominator[observed]
        np.testing.assert_allclose(counts,denominator)
        np.testing.assert_allclose(values,expected,equal_nan=True)
        self.assertTrue(np.isnan(values[0,0]))

    def test_support_parameter_validation_and_custom_radius(self):
        for invalid in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                NoEditConfig(smoothing_support_distance_m=invalid)
        grid=Grid(np.zeros(2),.05,np.ones((41,41),bool))
        row={'geometry':'valid','terminal_trials':1,'successes':1,'xy':[1,1]}
        narrow,_=gaussian_map(grid,[row],.1,max_support_distance_m=.1)
        wide,_=gaussian_map(grid,[row],.1,max_support_distance_m=.3)
        self.assertTrue(np.isnan(narrow[20,24]))
        self.assertEqual(wide[20,24],1.)

    def test_overlaps_accumulate_all_gaussian_weights_then_normalize(self):
        grid=Grid(np.zeros(2),.05,np.ones((21,21),bool))
        rows=[{'geometry':'valid','terminal_trials':2,'successes':2,'xy':[.4,.5]},
              {'geometry':'valid','terminal_trials':3,'successes':0,'xy':[.6,.5]}]
        values,counts=gaussian_map(grid,rows,.1,max_support_distance_m=.3)
        w=np.exp(-.5)
        self.assertAlmostEqual(counts[10,10],5*w)
        self.assertAlmostEqual(values[10,10],2/5)
        left_w=np.exp(-.5*(.05/.1)**2);right_w=np.exp(-.5*(.15/.1)**2)
        self.assertAlmostEqual(values[10,9],2*left_w/(2*left_w+3*right_w))
        reverse,_=gaussian_map(grid,list(reversed(rows)),.1,max_support_distance_m=.3)
        np.testing.assert_allclose(values,reverse,equal_nan=True)

    def scene(self):
        model=mujoco.MjModel.from_xml_string('''<mujoco><worldbody>
        <body name="table"><geom name="top" type="box" pos="0 0 .8" size=".5 .4 .05"/></body>
        <body name="cup" pos="0 0 .9"><freejoint/><geom type="sphere" size=".05"/></body>
        </worldbody></mujoco>''')
        data=mujoco.MjData(model);mujoco.mj_forward(model,data)
        catalog=[{'instance_id':name,'mjcf_body':name,'body_id':model.body(name).id}
                 for name in ('table','cup')]
        return SimpleNamespace(model=model,data=data,catalog=catalog)

    def test_elevated_support_blocks_heatmap_and_inflated_astar(self):
        sim=self.scene();task={'instance_id':'cup','target_body':'cup','operation':'pick','anchor_world':[0,0,.9]}
        obstacles=task_support_obstacles(sim,task)
        self.assertEqual(obstacles[0]['body'],'table')
        with patch('lastmile_dataflow.scenes.initialization.room_triangles',return_value=[]):
            heat=scene_grid(sim,[0,0],2.,.05,0.,0.,support_obstacles=obstacles)
            nav=scene_grid(sim,[0,0],2.,.05,.2,.02,support_obstacles=obstacles)
        self.assertFalse(heat.free[heat.cell([0,0])])
        self.assertTrue(heat.free[heat.cell([.6,0])])
        self.assertFalse(nav.free[nav.cell([.6,0])])
        path=search(nav,[-1,0],[1,0]);self.assertIsNotNone(path)
        self.assertTrue(all(abs(p[0])>.5 or abs(p[1])>.4 for p in path['xy']))
        row={'geometry':'valid','terminal_trials':1,'successes':1,'success_rate':1,'xy':[-.6,0]}
        values,_=gaussian_map(heat,[row],.5)
        self.assertTrue(np.isnan(values[heat.cell([-.5,0])]))
        with tempfile.TemporaryDirectory() as d:
            export_heatmap(d,heat,[row],[0,0,.9],.05,title='Synthetic support fixture (not physical validation)')
            before=nav.free.copy()
            export_navigation_map(d,nav,[0,0,.9],display_grid=heat)
            np.testing.assert_array_equal(nav.free,before)
            with np.load(Path(d,'success_heatmap.npz')) as z:
                self.assertNotIn('display_alpha',z.files)
                self.assertNotIn('kernel_strength',z.files)
                self.assertTrue(np.isnan(z['success_rate'][~heat.free]).all())
            self.assertNotIn('support / obstacle',Path(d,'navigation_grid.svg').read_text())
            self.assertIn('physical_footprints',Path(d,'navigation_display.json').read_text())
            self.assertIn('support / obstacle',Path(d,'success_heatmap.svg').read_text())
            self.assertIn('data:image/png;base64',Path(d,'index.html').read_text())

    def test_navigation_video_has_support_before_path(self):
        obstacle={'min':[-.5,-.4,.75],'max':[.5,.4,.85]}
        video=AnalysisVideo({'anchor_world':[0,0,1],'support_obstacles':[obstacle]}, {'xy':[-1,0]})
        frame=video.frame({'third_person_camera':np.zeros((200,300,3),np.uint8)},phase='initialize',time_s=0,
                          base_xy=[-1,0],target_height=1,initial_height=1)
        # Interior of the support rectangle, away from target glyph/text.
        self.assertEqual(frame[571,1070].tolist(),[73,61,45])

    def test_station_circles_use_metric_robot_radius(self):
        video=AnalysisVideo({'anchor_world':[0,0,1]}, {'xy':[-1,0]},
                            goal={'xy':[1,0]},footprint_radius_m=.4)
        frame=video.frame({'third_person_camera':np.zeros((200,300,3),np.uint8)},phase='initialize',time_s=0,
                          base_xy=[0,-1],target_height=1,initial_height=1)
        # 206 pixels / 4 metres: .4 m = 20.6 pixels; circles, not fixed-size dots.
        self.assertEqual(frame[555,1010].tolist(),[237,107,100])
        self.assertEqual(frame[555,1114].tolist(),[102,214,140])
        for radius in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                AnalysisVideo({}, {'xy':[0,0]},footprint_radius_m=radius)
