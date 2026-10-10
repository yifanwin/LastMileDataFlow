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
from lastmile_dataflow.stations.no_edit_statistics import aggregate,construction_rate,select_starts,success_goals
from lastmile_dataflow.navigation.astar import Grid,search,distance_field
from lastmile_dataflow.exporting.no_edit_heatmap import gaussian_map,export_heatmap
from lastmile_dataflow.validation.no_edit import attribute,evaluate_open
from lastmile_dataflow.validation.no_edit import mobile_constraint_failure,evaluate_mobile_pick,torso_tracking_error
from lastmile_dataflow.validation.pick import constraint_failure,evaluate_pick
from lastmile_dataflow.planning.curobo import locked_arm_config,NativePlanner
from lastmile_dataflow.tasks.raw_scene import discover_tasks,grasp_candidates
from lastmile_dataflow.workflows.no_edit import scene_manifest,task_seed,available_gpus,collect_task
from lastmile_dataflow.io import write_json
from lastmile_dataflow.runtime.no_edit_execution import clone_sim,run_raw_attempt,OperationFailure
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext
from lastmile_dataflow.recording.timing import GenerationTimer
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
        c=NoEditConfig(); self.assertEqual((c.radius_m,c.trials_per_station,c.min_successful_rollouts,c.start_count),(2.,5,1,3))
    def test_invalid_parameters_rejected(self):
        for changes in ({'spacing_m':0},{'trials_per_station':True},{'discard_below':float('nan')},
                        {'min_successful_rollouts':4},{'operations':['place']}):
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
    def test_scene_manifest_ignores_ceiling_and_counts_only_matched_sources(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'procthor-10k-val'; p.mkdir()
            for name in ('val_0.xml','val_0_ceiling.xml','val_0_metadata.json','val_1.xml'):
                (p/name).write_text('{}')
            rows=scene_manifest(p); self.assertEqual(len(rows),2)
            self.assertEqual(rows[1]['status'],'missing_metadata')
    def test_gpu_selection_uses_live_utilization_and_free_memory(self):
        with patch('subprocess.check_output',return_value='0, 80, 45000, 49140\n1, 0, 100, 49140\n2, 10, 12000, 49140\n'):
            self.assertEqual([r['index'] for r in available_gpus([0,1,2])],[1,2])
    def test_seed_stable_distinct_trials(self):
        self.assertEqual(task_seed(1,2,'t','s',0),task_seed(1,2,'t','s',0))
        self.assertNotEqual(task_seed(1,2,'t','s',0),task_seed(1,2,'t','s',1))


class StatisticsTests(unittest.TestCase):
    def test_unknown_and_filtered_are_not_failure(self):
        stations=[station('a',[0,0]),station('b',[1,0],'geometry_filtered')]
        trials=[{'station_id':'a','status':'success','attempt':'1'},
                {'station_id':'a','status':'infrastructure_error','attempt':'2'}]
        rows=aggregate(stations,trials,5)
        self.assertEqual(rows[0]['success_rate'],1); self.assertIsNone(rows[1]['success_rate'])
        self.assertEqual(construction_rate(rows,5,.05)['status'],'incomplete')
    def test_exact_five_percent_is_retained_for_rollout_search(self):
        rows=[rate_row(str(i),[i,0],1 if i==0 else 0) for i in range(4)]
        self.assertEqual(construction_rate(rows,5,.05)['status'],'eligible')
        rows.append(rate_row('4',[4,0],0))
        self.assertEqual(construction_rate(rows,5,.05)['status'],'discarded_low_success')
    def test_low_rate_first_and_spatial_ties(self):
        rows=[rate_row('a',[0,0],0),rate_row('b',[.1,0],0),rate_row('c',[3,0],0),rate_row('d',[0,3],0),rate_row('e',[8,8],1)]
        picked=select_starts(rows,3)
        self.assertEqual(len(picked),3); self.assertTrue(all(r['success_rate']==0 for r in picked))
        self.assertIn('c',[r['station_id'] for r in picked]); self.assertIn('d',[r['station_id'] for r in picked])
    def test_can_select_one_or_two_starts(self):
        self.assertEqual(len(select_starts([rate_row('a',[0,0],0)],3)),1)
        self.assertEqual(len(success_goals([rate_row('a',[0,0],0),rate_row('b',[1,0],3)])),1)


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
    def test_heatmap_generates_embedded_png_svg_and_raw_values(self):
        with tempfile.TemporaryDirectory() as d:
            grid=Grid(np.zeros(2),.5,np.ones((7,7),bool))
            path=export_heatmap(d,grid,[rate_row('a',[1,1],3)],[1,1,1],.5,title='synthetic fixture only')
            self.assertGreater(path.stat().st_size,1000)
            self.assertIn('data:image/png;base64',Path(d,'index.html').read_text())
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
    def test_no_solution_keeps_empty_trajectory_and_no_execution_video(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d); root=Path(d); sim.freeze(root/'scene')
            task={'task_id':'t','operation':'pick','target_body':'target'}
            s={**station('s',[0,0]),'initial':robot.initial,'base':[0,0,0]}
            with patch('lastmile_dataflow.runtime.no_edit_execution.manipulate',side_effect=OperationFailure('outside_conservative_reach_bound')):
                row=run_raw_attempt(sim,task,s,[],d,NoEditConfig(),CollectionConfig(output_dir=d),root,'test',seed=1)
            self.assertEqual(row['status'],'planning_no_solution'); self.assertEqual(row['attribution']['category'],'Reachability')
            self.assertEqual((Path(row['attempt'])/'trajectory.jsonl').read_text(),'')
            self.assertFalse((Path(row['attempt'])/'videos').exists()); sim.close()
    def test_open_requires_real_joint_progress_and_contact_hold(self):
        task={'joint_initial':0,'joint_goal':.1}
        samples=[{'time_s':i*.004,'joint_value':.1,'finger_forces_n':{'1':1,'2':1}} for i in range(276)]
        self.assertEqual(evaluate_open(task,samples,1.)['status'],'success')
        samples[-1]['finger_forces_n']={}
        self.assertEqual(evaluate_open(task,samples,1.)['status'],'failure')
    def test_ik_failure_without_diagnostic_is_not_claimed_unreachable(self):
        self.assertEqual(attribute('planning_no_solution')['category'],'PlanningFailure')


class WorkflowRetentionTests(unittest.TestCase):
    def test_final_policy_keeps_one_two_or_three_successful_segments_not_zero(self):
        # Orchestration fixture ONLY: no fabricated physics/camera evidence is
        # written to a real collection, and no real success is claimed here.
        for successes in range(4):
            with self.subTest(successes=successes), tempfile.TemporaryDirectory() as d:
                root=Path(d)/'val_0'; task_path=root/'tasks'/'t'; task_path.mkdir(parents=True)
                write_json(root/'robot_geometry.json',{'radius_m':.4})
                task={'task_id':'t','operation':'pick','target_body':'target',
                      'anchor_world':[0.,0.,1.], 'instruction':'fixture instruction'}
                stations=[station('a',[0,0]),station('b',[0,1]),station('c',[1,0]),station('g',[2,2])]
                write_json(task_path/'stations.json',stations)
                trials=[{'station_id':s['station_id'],'trial':i,
                         'status':'success' if s['station_id']=='g' else 'failure',
                         'attribution':{'category':'MissedGrasp'},'control':{'seed':1},
                         'attempt':'fixture-only'} for s in stations for i in range(5)]
                write_json(task_path/'trials.json',trials)
                calls=[]
                def fake_rollout(*args,**kwargs):
                    calls.append(kwargs)
                    return {'status':'success' if len(calls)<=successes else 'failure',
                            'navigation':{'status':'success'},'attempt':'fixture-only'}
                grid=Grid(np.array([-1.,-1.]),1.,np.ones((6,6),bool))
                with patch('lastmile_dataflow.tasks.raw_scene.grasp_candidates',return_value=[]), \
                     patch('lastmile_dataflow.navigation.astar.scene_grid',return_value=grid), \
                     patch('lastmile_dataflow.exporting.no_edit_heatmap.export_heatmap'), \
                     patch('lastmile_dataflow.runtime.no_edit_execution.run_raw_attempt',side_effect=fake_rollout), \
                     patch('lastmile_dataflow.workflows.no_edit.check_disk'):
                    result=collect_task(None,task,d,NoEditConfig(),CollectionConfig(output_dir=d),root)
                self.assertEqual(len(calls),3)
                self.assertEqual(result['successful_rollouts'],successes)
                self.assertEqual(result['dataset_eligible'],successes>=1)
                self.assertEqual(result['status'],'retained' if successes else 'no_successful_rollout')


class MobileManipulationTests(unittest.TestCase):
    def test_mobile_config_releases_exactly_base_and_selected_arm(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=make_sim(d)
            names=sum((list(sim.robot.groups[g]) for g in ('base','torso','head','left_arm','right_arm')),[])
            template={'robot_cfg':{'kinematics':{'cspace':{'joint_names':names,'retract_config':[0.]*len(names)},
                                                'lock_joints':{'base_x':0.,'base_y':0.,'base_theta':0.}}}}
            mobile=locked_arm_config(template,sim,'left',Path(d),mobile_base=True)['robot_cfg']['kinematics']
            fixed=locked_arm_config(template,sim,'left',Path(d))['robot_cfg']['kinematics']
            self.assertEqual(mobile['cspace']['joint_names'],['base_x','base_y','base_theta']+[f'left_arm_{i}' for i in range(7)])
            self.assertFalse(set(sim.robot.groups['base'])&set(mobile['lock_joints']))
            self.assertTrue(set(sim.robot.groups['right_arm'])<=set(mobile['lock_joints']))
            self.assertEqual(len(fixed['cspace']['joint_names']),7)
            self.assertTrue(set(sim.robot.groups['base'])<=set(fixed['lock_joints']))
            sim.close()
    def test_local_mobile_waypoint_uses_rotated_world_reference(self):
        planner=NativePlanner.__new__(NativePlanner); planner.mobile_base=True
        planner.base=np.array([[0.,-1.,0.,3.],[1.,0.,0.,4.],[0.,0.,1.,.005],[0.,0.,0.,1.]])
        actual=planner.base_target_world(np.r_[.4,.2,.3,np.zeros(7)])
        np.testing.assert_allclose(actual,[2.8,4.4,np.pi/2+.3])
    def test_mobile_execution_submits_nonzero_base_and_arm_not_fixed(self):
        with tempfile.TemporaryDirectory() as d:
            sim,_=make_sim(d)
            ctx=ContinuousContext.__new__(ContinuousContext); ctx.sim=sim;ctx.config=NoEditConfig();ctx.monitor=None
            calls=[];ctx.tick=lambda a,**kw:calls.append((a.copy(),kw))
            planner=SimpleNamespace(names=('base_x','base_y','base_theta',*(f'left_arm_{i}' for i in range(7))),
                                    base_target_world=lambda p:np.array([.03,.01,.04]))
            point=np.r_[.03,.01,.04,sim.robot.group('left_arm')+.1]
            ctx.mobile_tick(planner,point,'left',-.05,0.)
            a,kw=calls[0];self.assertFalse(kw['fixed_base']);self.assertGreater(np.linalg.norm(a[:3]),0.)
            self.assertGreater(np.linalg.norm(a[3:10]),0.);self.assertLessEqual(np.linalg.norm(a[:2]),.01+1e-9)
            sim.close()
    def test_mobile_validator_allows_base_motion_but_fixed_protocol_still_rejects(self):
        from test_stations import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        for row in samples: row['base']=[.3,.2,.5]
        self.assertEqual(evaluate_pick(samples,initial)['reason'],'base_drift')
        mobile=evaluate_mobile_pick(samples,initial)
        self.assertEqual((mobile['status'],mobile['protocol']),('success','mobile-pick-v2'))
        samples[-1]['base']=[2.1,0.,0.]
        self.assertEqual(evaluate_mobile_pick(samples,initial)['reason'],'base_outside_workspace')
    def test_mobile_validator_keeps_collision_and_idle_arm_checks(self):
        from test_stations import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        samples[-1]['illegal']=['robot_environment_collision']
        self.assertEqual(evaluate_mobile_pick(samples,initial)['reason'],'robot_environment_collision')
        samples[-1]['illegal']=[];samples[-1]['idle_arm'][0]=.02
        self.assertEqual(evaluate_mobile_pick(samples,initial)['reason'],'idle_arm_drift')
    def test_mobile_torso_error_is_telemetry_but_strict_protocol_unchanged(self):
        from test_stations import evidence
        initial,samples=evidence();initial.update(workspace_center_xy=[0.,0.],workspace_radius_m=2.)
        samples[-1]['torso'][0]=.03
        self.assertAlmostEqual(torso_tracking_error(samples[-1]),.03)
        self.assertEqual(evaluate_pick(samples,initial)['reason'],'torso_protocol')
        self.assertEqual(evaluate_mobile_pick(samples,initial)['status'],'success')
        samples[-1]['illegal']=['robot_environment_collision']
        self.assertEqual(evaluate_mobile_pick(samples,initial)['reason'],'robot_environment_collision')
    def test_mobile_torso_nonfinite_is_still_invalid_evidence(self):
        from test_stations import evidence
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
    def test_failed_executed_station_trial_keeps_three_camera_video(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d);root=Path(d);sim.freeze(root/'scene')
            task={'task_id':'t','operation':'pick','target_body':'target'}
            s={**station('s',[0,0]),'initial':robot.initial,'base':[0,0,0]}
            def fail_after_execution(ctx,*args):
                ctx.tick(ctx.sim.robot.neutral_action())
                raise OperationFailure('tcp_tracking_timeout')
            frames={name:np.zeros((32,32,3),dtype=np.uint8) for name in (*robot.cameras,'third_person_camera')}
            with patch('lastmile_dataflow.runtime.no_edit_execution.manipulate',side_effect=fail_after_execution), \
                 patch('lastmile_dataflow.runtime.simulation.Simulation.render',return_value=frames):
                row=run_raw_attempt(sim,task,s,[],d,NoEditConfig(),CollectionConfig(output_dir=d),root,'fixture-video',seed=1)
            self.assertEqual(row['status'],'failure');self.assertEqual(row['video_status'],'recorded')
            video=json.loads(Path(row['videos']).read_text())
            self.assertEqual(len(video['cameras']),4)
            self.assertTrue(Path(row['third_person_video']).is_file())
            for camera,entry in video['cameras'].items():
                self.assertEqual(len(entry['frame_times_s']),2)
                self.assertTrue((Path(row['attempt'])/entry['path']).is_file())
            sim.close()


class ObserverAndTimingTests(unittest.TestCase):
    def test_timer_records_finish_and_keeps_resume_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'timing.json';first=GenerationTimer(p)
            finished=first.update('incomplete',final=True)
            self.assertIsNotNone(finished['ended_at_utc'])
            resumed=GenerationTimer(p,resume=True);current=resumed.update()
            self.assertEqual(current['started_at_utc'],finished['started_at_utc'])
            self.assertEqual(current['session_count'],2)
            self.assertIsNone(current['ended_at_utc'])
            self.assertGreaterEqual(current['elapsed_active_s'],finished['elapsed_active_s'])
    def test_timer_marks_abrupt_prior_session_unknown_not_completed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'timing.json';GenerationTimer(p)
            timer=GenerationTimer(p,resume=True)
            self.assertEqual(timer.data['sessions'][0]['status'],'interrupted')
            self.assertFalse(timer.data['sessions'][0]['termination_time_known'])
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
