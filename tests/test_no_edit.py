"""Software regression only; synthetic observations are not real task evidence."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

from helpers import make_sim
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.stations.no_edit_sampling import disk_samples,place_frozen_station
from lastmile_dataflow.stations.no_edit_statistics import aggregate
from lastmile_dataflow.navigation.astar import Grid,search,distance_field
from lastmile_dataflow.exporting.no_edit_heatmap import gaussian_map,export_heatmap
from lastmile_dataflow.validation.no_edit import attribute,evaluate_open
from lastmile_dataflow.validation.no_edit import mobile_constraint_failure,evaluate_mobile_pick,torso_tracking_error
from lastmile_dataflow.validation.pick import constraint_failure,evaluate_pick
from lastmile_dataflow.tasks.raw_scene import discover_tasks,grasp_candidates
from lastmile_dataflow.io import write_json
from lastmile_dataflow.runtime.no_edit_execution import clone_sim,run_raw_attempt,OperationFailure
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext
from lastmile_dataflow.recording.third_person import ThirdPersonCamera
from lastmile_dataflow.recording.analysis_video import AnalysisVideo
from lastmile_dataflow.config import CollectionConfig


def station(name,xy,geometry='valid'):
    return {'station_id':name,'xy':xy,'geometry':geometry}


def rate_row(name,xy,successes,n=5):
    return {**station(name,xy),'successes':successes,'terminal_trials':n,
            'success_rate':successes/n,'complete':True}


class RawConfigAndSamplingTests(unittest.TestCase):
    def test_defaults_match_user(self):
        c=NoEditConfig(); self.assertEqual((c.radius_m,c.trials_per_station,c.start_count),(2.,5,3))
    def test_invalid_parameters_rejected(self):
        for changes in ({'spacing_m':0},{'trials_per_station':True},
                        {'operations':['place']}):
            with self.assertRaises(ValueError): NoEditConfig(**changes)
    def test_zero_optional_geometry_offsets_are_legal(self):
        c=NoEditConfig(approach_offset_m=0., navigation_margin_m=0.)
        self.assertEqual(c.approach_offset_m,0.)
        with self.assertRaises(ValueError): NoEditConfig(navigation_margin_m=-.01)
    def test_uniform_disk_and_reproducibility(self):
        rows=disk_samples([1,2,3],2,.5)
        self.assertEqual(rows,disk_samples([1,2,3],2,.5))
        self.assertTrue(all(np.linalg.norm(np.array(r['xy'])-[1,2])<=2+1e-9 for r in rows))
        self.assertEqual(len({tuple(r['grid_index']) for r in rows}),len(rows))


class StatisticsTests(unittest.TestCase):
    def test_unknown_and_filtered_are_not_failure(self):
        stations=[station('a',[0,0]),station('b',[1,0],'geometry_filtered')]
        trials=[{'station_id':'a','status':'success','attempt':'1'},
                {'station_id':'a','status':'infrastructure_error','attempt':'2'}]
        rows=aggregate(stations,trials,5)
        self.assertEqual(rows[0]['success_rate'],1); self.assertIsNone(rows[1]['success_rate'])
        self.assertFalse(rows[0]['complete'])


class MapNavigationTests(unittest.TestCase):
    def test_astar_obstacles_and_no_corner_cut(self):
        grid=Grid(np.zeros(2),1.,np.array([[1,0],[0,1]],bool))
        self.assertIsNone(search(grid,[0,0],[1,1]))
        free=np.ones((5,5),bool); free[:4,2]=False; grid=Grid(np.zeros(2),1.,free)
        path=search(grid,[0,0],[4,0]); self.assertIsNotNone(path)
        self.assertTrue(all(free[tuple(cell)] for cell in path['cells']))
    def test_gaussian_does_not_cross_wall_or_fill_unknown_with_zero(self):
        free=np.ones((7,7),bool); free[:,3]=False; grid=Grid(np.zeros(2),.5,free)
        values,counts=gaussian_map(grid,[rate_row('a',[.5,1.5],5)],.5)
        self.assertEqual(values[3,1],1.); self.assertTrue(np.isnan(values[3,5]))
        self.assertTrue(np.isnan(values[:,3]).all())
    def test_weight_successes_and_trials_separately(self):
        grid=Grid(np.zeros(2),1.,np.ones((3,3),bool))
        rows=[rate_row('a',[1,1],1,1),rate_row('b',[1,1],0,5)]
        values,_=gaussian_map(grid,rows,1.)
        self.assertAlmostEqual(values[1,1],1/6)
    def test_heatmap_generates_labeled_png_svg_without_html(self):
        with tempfile.TemporaryDirectory() as d:
            grid=Grid(np.zeros(2),.5,np.ones((7,7),bool))
            path=export_heatmap(d,grid,[rate_row('a',[1,1],3)],[1,1,1],.5,title='synthetic fixture only')
            self.assertGreater(path.stat().st_size,1000)
            self.assertFalse(Path(d,'index.html').exists())
            self.assertTrue(Path(d,'success_heatmap_stations.png').is_file())
            self.assertTrue(Path(d,'success_heatmap_stations.svg').is_file())
            self.assertTrue(Path(d,'success_heatmap.svg').is_file())


class TaskAndExecutionTests(unittest.TestCase):
    def test_actual_free_joint_and_dimensions_not_category_alone(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d)
            sim.catalog[0].update(asset_id='fixture',category='Cup',room_id=1)
            tasks,skipped=discover_tasks(sim,{},NoEditConfig(gripper_width_m=.21))
            self.assertTrue(any(t['operation']=='pick' for t in tasks)); sim.close()
    def test_procedural_grasps_are_proper_rotations(self):
        task={'asset_id':'fixture','operation':'pick','target_body':'target','local_bounds':[[-.01,-.02,-.03],[.01,.02,.03]]}
        with tempfile.TemporaryDirectory() as d:
            candidates=grasp_candidates(task,d)
            self.assertTrue(candidates)
            for row in candidates: self.assertAlmostEqual(np.linalg.det(row['pose_local'][:3,:3]),1.)
    def test_diagnostic_clone_does_not_reset_running_simulation(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d); sim.freeze(Path(d)/'scene'); sim.begin()
            clone=clone_sim(sim,target='target'); self.assertFalse(clone.started)
            clone.robot.initialize(); self.assertTrue(sim.started)
            clone.close(); sim.close()
    def test_post_begin_placement_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d); sim.begin()
            with self.assertRaises(RuntimeError): place_frozen_station(sim,{'initial':robot.initial})
            sim.close()


    def test_open_requires_real_joint_progress_and_contact_hold(self):
        task={'joint_initial':0,'joint_goal':.1}
        samples=[{'time_s':i*.004,'joint_value':.1,'finger_forces_n':{'1':1,'2':1}} for i in range(276)]
        self.assertEqual(evaluate_open(task,samples,1.)['status'],'success')
        samples[-1]['finger_forces_n']={}
        self.assertEqual(evaluate_open(task,samples,1.)['status'],'failure')
    def test_ik_failure_without_diagnostic_is_not_claimed_unreachable(self):
        self.assertEqual(attribute('planning_no_solution')['category'],'PlanningFailure')




class MobileManipulationTests(unittest.TestCase):
    def test_mobile_validator_allows_base_motion_but_fixed_protocol_still_rejects(self):
        from helpers import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        for row in samples: row['base']=[.3,.2,.5]
        self.assertEqual(evaluate_pick(samples,initial)['reason'],'base_drift')
        mobile=evaluate_mobile_pick(samples,initial)
        self.assertEqual((mobile['status'],mobile['protocol']),('success','mobile-pick-v2'))
        samples[-1]['base']=[2.1,0.,0.]
        self.assertEqual(evaluate_mobile_pick(samples,initial)['reason'],'base_outside_workspace')
    def test_mobile_validator_records_collision_and_keeps_idle_arm_check(self):
        from helpers import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        samples[-1]['illegal']=['robot_environment_collision']
        self.assertEqual(evaluate_mobile_pick(samples,initial)['status'],'success')
        samples[-1]['illegal']=[];samples[-1]['idle_arm'][0]=.02
        self.assertEqual(evaluate_mobile_pick(samples,initial)['reason'],'idle_arm_drift')
    def test_mobile_torso_error_is_telemetry_but_strict_protocol_unchanged(self):
        from helpers import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        samples[-1]['torso'][0]=.03
        self.assertAlmostEqual(torso_tracking_error(samples[-1]),.03)
        self.assertEqual(evaluate_pick(samples,initial)['reason'],'torso_protocol')
        self.assertEqual(evaluate_mobile_pick(samples,initial)['status'],'success')
        samples[-1]['illegal']=['robot_environment_collision']
        self.assertEqual(evaluate_mobile_pick(samples,initial)['status'],'success')
    def test_mobile_torso_nonfinite_is_still_invalid_evidence(self):
        from helpers import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        samples[-1]['torso'][0]=float('nan')
        self.assertEqual(evaluate_mobile_pick(samples,initial)['status'],'infrastructure_error')
    def test_torso_adjust_telemetry_uses_measured_h(self):
        row={'phase':'torso_adjust','torso':[0,.3,-.6,.3,0,0],'command_h':.4}
        self.assertEqual(torso_tracking_error(row),0.)
        row['phase']='pregrasp'
        self.assertAlmostEqual(torso_tracking_error(row),.2)
    def test_empty_asset_grasp_array_is_explicit_procedural_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'grasps'/'droid'/'fixture';p.mkdir(parents=True)
            np.savez(p/'fixture_grasps_filtered.npz',transforms=np.array([]))
            task={'asset_id':'fixture','operation':'pick','target_body':'target',
                  'local_bounds':[[-.01,-.02,-.03],[.01,.02,.03]]}
            rows=grasp_candidates(task,d)
            self.assertTrue(rows);self.assertEqual(rows[0]['source'],'procedural_bbox_pinch_v1')
            self.assertEqual(len(rows[0]['empty_asset_grasp_sources']),1)


class ObserverAndTimingTests(unittest.TestCase):
    def test_reusable_observer_fits_robot_target_without_state_or_model_changes(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=make_sim(d);q=sim.data.qpos.copy();fovy=float(sim.model.vis.global_.fovy)
            cam=sim.enable_third_person();fit,robot,target=cam.framing(sim,320,240)
            self.assertTrue(robot and target);self.assertGreater(fit.distance_m,0.)
            np.testing.assert_array_equal(q,sim.data.qpos)
            self.assertEqual(fovy,float(sim.model.vis.global_.fovy));sim.close()
    def test_analysis_fourth_frame_does_not_mutate_three_robot_frames(self):
        frames={name:np.zeros((32,32,3),dtype=np.uint8) for name in
                ('head_camera','wrist_camera_l','wrist_camera_r','third_person_camera')}
        view=AnalysisVideo({'anchor_world':[0,0,1],'instruction':'fixture only'},station('s',[0,0]))
        image=view.frame(frames,phase='navigation',time_s=1.,base_xy=[.1,0],target_height=1.,initial_height=1.)
        self.assertEqual(image.shape,(720,1280,3));self.assertTrue(image.any())
        self.assertTrue(all(not rgb.any() for rgb in frames.values()))


if __name__=='__main__': unittest.main()
