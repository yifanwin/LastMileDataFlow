"""Synthetic fixtures verify software gates, never substitute real GPU/pick evidence."""
import copy
from dataclasses import replace,asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import mujoco
from helpers import make_sim
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.io import write_json,file_digest
from lastmile_dataflow.stations.config import StationConfig
from lastmile_dataflow.stations.sampling import coarse_samples,refinements,place_base,filter_station
from lastmile_dataflow.planning.curobo import locked_arm_config,load_grasps
from lastmile_dataflow.integrations.waypoints import PlanResult
from lastmile_dataflow.validation.pick import evaluate_pick,PROTOCOL
from lastmile_dataflow.validation.stations import case1_verdict,audit_station_attempt
from lastmile_dataflow.stations.execution import run_station_attempt,Budget,BudgetStop
from lastmile_dataflow.agents.http_vision import parse_review,read_settings,RejectRedirects


def config(**changes):
    args=dict(target='target',asset_id='fixture',grasp_path='/nonexistent/grasp.npz',grasp_sha256='a'*64,robot_planner_dir='/nonexistent/planner')
    args.update(changes); return StationConfig(**args)


def evidence():
    initial={'base':[0,0,0],'head':[0,0],'idle_arm':[0]*7,'target':'cup','height_m':.8,'fingers':[1,2]}
    samples=[]
    for i in range(551):
        samples.append({'time_s':i*.004,'phase':'hold','command_h':0.,'base':[0,0,0],'head':[0,0],'idle_arm':[0]*7,
                        'torso':[0]*6,'target':'cup','height_m':.9,'support_contact':False,'finger_forces_n':{'1':1.,'2':1.},
                        'relative_pose':np.eye(4).tolist(),'illegal':[],'sampler':'force-aware-v3'})
    return initial,samples


class ConfigSamplingTests(unittest.TestCase):
    def test_config_rejects_unsupported_case_and_weak_protocol(self):
        for kwargs in ({'case_type':'case2'},{'case_type':'case3'},{'protocol':'lenient'},{'max_plans':True},{'grasp_ids':[-1]}, {'probes':[[0,0,float('nan')]]}):
            with self.assertRaises(ValueError): config(**kwargs)
    def test_case15_requires_frozen_side_points_and_labels_them(self):
        sides={'side_a':[0,0,0],'side_b':[1,0,0]}
        with self.assertRaises(ValueError): config(case_type='case1.5')
        for bad in ({'side_d':[0,0,0]},{'side_a':[0,0]},{'side_a':[0,0,float('nan')]}):
            with self.assertRaises(ValueError): config(case_type='case1.5',side_points=bad)
        with self.assertRaises(ValueError):
            config(case_type='case1.5',side_points={'side_a':[0,0,0]},side_roles={'side_b':'narrow'})
        cfg=config(case_type='case1.5',side_points=sides,side_roles={'side_a':'narrow','side_b':'far'})
        self.assertEqual(cfg.side_roles['side_a'],'narrow')
    def test_case15_per_side_verdict_never_promotes_partial_evidence(self):
        from lastmile_dataflow.validation.stations import case1_5_verdict,side_assignment
        points={'side_a':[0,0,0],'side_b':[2,0,0]}
        samples=[{'station_id':'S000','base':[.05,0,0]},{'station_id':'S001','base':[1.9,0,0]},
                 {'station_id':'S002','base':[9,9,0]}]
        self.assertEqual(side_assignment(samples,points,.5),{'S000':'side_a','S001':'side_b','S002':None})
        rows=[{'station_id':'S000','base':[.05,0,0],'side':'side_a','execution':'failure','status':'executed_failure','planning':'no_solution','attempt':'a0'},
              {'station_id':'S001','base':[1.9,0,0],'side':'side_b','execution':'success','status':'executed_success','planning':'success','attempt':'a1'}]
        verdict=case1_5_verdict(rows,[0,0,0],points,{'side_a':'narrow','side_b':'far'})
        # A fixed-base success on one side plus a failure on another is a per-side difference, and
        # the overall case condition must stay short of `pass`.
        self.assertEqual(verdict['status'],'partial')
        self.assertEqual(verdict['per_side']['side_a']['status'],'failed')
        self.assertEqual(verdict['per_side']['side_b']['status'],'succeeded')
        self.assertEqual(verdict['per_side']['side_a']['role'],'narrow')
        self.assertEqual(verdict['narrow_side_passage']['status'],'unknown')
        self.assertEqual(verdict['success_witnesses'] if 'success_witnesses' in verdict else None,None)
        only_success=case1_5_verdict([rows[1]], [0,0,0], points)
        self.assertEqual(only_success['status'],'unknown')
        no_success=case1_5_verdict([rows[0]], [0,0,0], points)
        self.assertEqual(no_success['status'],'unknown')
    def test_sampling_is_deterministic_bounded_with_yaw(self):
        cfg=config(max_candidates=12,probes=[[0,0,0],[0,0,2*np.pi]])
        a=coarse_samples(cfg,[1,0,1],[0,0,0]); b=coarse_samples(cfg,[1,0,1],[0,0,0])
        self.assertEqual(a,b); self.assertEqual(len(a),12); self.assertEqual(len({r['station_id'] for r in a}),12)
    def test_refine_only_measured_decision_boundaries(self):
        cfg=config(max_refinements=2)
        self.assertEqual(refinements(cfg,[{'execution':'success','base':[0,0,0],'station_id':'S000'}],[]),[])
        rows=[{'execution':'success','base':[0,0,0],'station_id':'S000'},{'execution':'not_executed','planning':'no_solution','base':[.5,0,0]}]
        self.assertEqual(len(refinements(cfg,rows,[{'base':[0,0,0]}])),2)
    def test_base_only_preparation_and_no_post_begin_placement(self):
        with tempfile.TemporaryDirectory() as d:
            sim,r=make_sim(d); old=sim.data.qpos.copy(); place_base(sim,[1,1,.3]); mask=np.ones(sim.model.nq,bool)
            mask[[sim.robot.addresses[n] for n in sim.robot.groups['base']]]=False
            np.testing.assert_array_equal(old[mask],sim.data.qpos[mask]); sim.begin()
            with self.assertRaises(RuntimeError): place_base(sim,[2,2,0])
            sim.close()
    def test_fixed_base_servo_does_not_follow_feedback_drift(self):
        with tempfile.TemporaryDirectory() as d:
            sim,r=make_sim(d); a=sim.robot.neutral_action();sim.robot.apply(a,fixed_base=True)
            sim.data.qpos[sim.robot.addresses['base_x']]+=.01
            cmd=sim.robot.apply(a,fixed_base=True);self.assertEqual(cmd['joint_targets']['base'][0],0.)
            sim.close()
    def test_floor_visual_occlusion_does_not_reject_real_floor(self):
        from lastmile_dataflow.scenes.initialization import floor_support
        with tempfile.TemporaryDirectory() as d:
            sim,r=make_sim(d)
            self.assertTrue(floor_support(sim,[0,0])); sim.close()
    def test_pi_limit_is_not_silently_clipped(self):
        from lastmile_dataflow.robots.action import InvalidAction
        with tempfile.TemporaryDirectory() as d:
            sim,r=make_sim(d)
            with self.assertRaises(InvalidAction): place_base(sim,[0,0,np.pi])
            np.testing.assert_array_equal(sim.robot.group('base'),[0,0,0])
            sim.close()
    def test_disabled_refinements_stays_disabled(self):
        rows=[{'execution':'success','base':[0,0,0],'station_id':'S000'}, {'execution':'failure','base':[.1,0,0]}]
        self.assertEqual(refinements(config(max_refinements=0),rows,[]),[])
    def test_refinement_cannot_confuse_controller_changes_with_spatial_boundary(self):
        success={'execution':'success','base':[0,0,0],'station_id':'S000',
                 'arm':'left','torso_h':.738,'grasp_row':794,'approach_offset_m':.01}
        failure={**success,'execution':'failure','base':[.3,0,0],'station_id':'S001'}
        for key,value in [('arm','right'),('torso_h',0.),('grasp_row',795),('approach_offset_m',0.)]:
            self.assertEqual(refinements(config(),[success,{**failure,key:value}],[]),[],key)
        self.assertEqual(refinements(config(),[success,{**failure,'base':[0,0,0]}],[]),[])
        self.assertTrue(refinements(config(),[success,failure],[]))
    def test_run_budget_does_not_reset_between_attempts(self):
        b=Budget(config(max_plans=1,max_executions=1)); b.plan(); b.execute()
        with self.assertRaises(BudgetStop):b.plan()
        with self.assertRaises(BudgetStop):b.execute()


class PickEvidenceTests(unittest.TestCase):
    def test_two_fingers_lift_unupported_hold_succeeds(self):
        initial,rows=evidence();self.assertEqual(evaluate_pick(rows,initial)['status'],'success')
    def test_static_lift_or_one_finger_never_succeeds(self):
        for mode in ('support','finger','lift','force'):
            initial,rows=evidence()
            for row in rows:
                if mode=='support':row['support_contact']=True
                if mode=='finger':row['finger_forces_n']={'1':1.}
                if mode=='lift':row['height_m']=.84
                if mode=='force':row['finger_forces_n']={'1':0.,'2':0.}
            self.assertNotEqual(evaluate_pick(rows,initial)['status'],'success',mode)
    def test_early_protocol_failure_remains_failure(self):
        initial,rows=evidence();rows[3]['base'][0]=.003
        self.assertEqual(evaluate_pick(rows,initial)['reason'],'base_drift')
        initial,rows=evidence(); rows[3]['illegal']=['robot_environment_collision']
        self.assertEqual(evaluate_pick(rows,initial)['status'],'failure')
    def test_contact_break_restarts_tail_hold(self):
        initial,rows=evidence();rows[300]['finger_forces_n']={}
        self.assertEqual(evaluate_pick(rows,initial)['status'],'failure')
    def test_trace_gaps_and_bad_state_are_not_physical_failure(self):
        initial,rows=evidence();rows.pop(5)
        self.assertEqual(evaluate_pick(rows,initial)['status'],'infrastructure_error')
        initial,rows=evidence();rows[5]['relative_pose'][0][0]=2
        self.assertEqual(evaluate_pick(rows,initial)['status'],'infrastructure_error')
    def test_relative_slip_blocks_success(self):
        initial,rows=evidence()
        for i,row in enumerate(rows): row['relative_pose'][0][3]=i*.0001
        self.assertEqual(evaluate_pick(rows,initial)['status'],'failure')
    def test_case_needs_difficult_origin_and_real_success(self):
        fail={'base':[0,0,0],'execution':'not_executed','planning':'no_solution','attempt':'a'}
        success={'base':[1,0,0],'execution':'success','planning':'success','attempt':'b'}
        self.assertEqual(case1_verdict([fail],[0,0,0])['status'],'unknown')
        self.assertEqual(case1_verdict([fail,success],[0,0,0])['status'],'pass')
        self.assertEqual(case1_verdict([success],[1,0,0])['status'],'fail')
        self.assertEqual(case1_verdict([success],[0,0,0])['status'],'unknown')


class PlanningGateTests(unittest.TestCase):
    def test_no_solution_has_no_waypoints(self):
        with self.assertRaises(ValueError): PlanResult('no_solution',('x',),((0,),),{})
    def test_grasp_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            sim,r=make_sim(d); p=Path(d)/'grasp.npz'; np.savez(p,transforms=np.eye(4)[None])
            cfg=config(grasp_path=str(p),grasp_sha256=file_digest(p),grasp_ids=[0])
            with self.assertRaises(ValueError):load_grasps(cfg,sim)
            sim.close()
    def test_planning_only_attempt_empty_trajectory_no_video_and_auditable(self):
        class NoSolution:
            def __init__(self,*args): pass
            def plan(self,goal): return PlanResult('no_solution',tuple(f'left_arm_{i}' for i in range(7)),(),{'status':'IK_FAIL'})
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d); scene=Path(d)/'scene'; sim.freeze(scene); sim.close()
            cfg=config(torso_heights=[0],render_video=False); coll=CollectionConfig(output_dir=d)
            sample={'station_id':'S000','base':[0,0,0],'level':'probe'}
            with patch('lastmile_dataflow.stations.execution.load_grasps',return_value={794:np.eye(4)}),patch('lastmile_dataflow.stations.execution.filter_station',return_value={'status':'valid'}):
                path=run_station_attempt(scene,robot,cfg,coll,sample,'left',0,794,d,'empty',Budget(cfg),planner_factory=NoSolution)
            self.assertEqual((path/'trajectory.jsonl').read_text(),'')
            self.assertEqual(json.loads((path/'result.json').read_text())['status'],'planning_no_solution')
            self.assertFalse((path/'videos').exists()); self.assertTrue(audit_station_attempt(path)['valid'])
            (path/'planning.json').write_text('[]')
            self.assertFalse(audit_station_attempt(path)['valid'])
    def test_illegal_initial_joint_is_geometry_not_infrastructure(self):
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d); scene=Path(d)/'scene'; sim.freeze(scene); sim.close()
            cfg=config(render_video=False)
            path=run_station_attempt(scene,robot,cfg,CollectionConfig(output_dir=d),{'station_id':'S000','base':[0,0,np.pi],'level':'probe'},'left',0,794,d,'pi',Budget(cfg))
            result=json.loads((path/'result.json').read_text())
            self.assertEqual(result['status'],'geometry_filtered'); self.assertEqual(result['executed_steps'],0)
            self.assertTrue(audit_station_attempt(path)['valid'])
    def test_real_steps_of_synthetic_protocol_failure_are_preserved(self):
        class Planned:
            def __init__(self,*args): pass
            def plan(self,goal): return PlanResult('success',tuple(f'left_arm_{i}' for i in range(7)),((.5,0,0,-2.3,0,-.5,0),),{})
        class SyntheticFailureMonitor:
            def __init__(self,sim,side):
                self.sim=sim; self.phase='torso_adjust'; self.command_h=0.
                self.initial={'base':sim.robot.group('base').tolist(),'head':sim.robot.group('head').tolist(),'idle_arm':sim.robot.group('right_arm').tolist(),'target':'target','height_m':.15,'fingers':[1,2]}
            def sample(self):
                return {'time_s':float(self.sim.data.time),'phase':self.phase,'command_h':self.command_h,
                        'base':self.initial['base'],'head':self.initial['head'],'idle_arm':self.initial['idle_arm'],'torso':[0]*6,
                        'target':'target','height_m':.15,'support_contact':True,'finger_forces_n':{},'relative_pose':np.eye(4).tolist(),
                        'illegal':['synthetic_protocol_failure'],'sampler':'force-aware-v3'}
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d);scene=Path(d)/'scene';sim.freeze(scene);sim.close()
            cfg=config(torso_heights=[0],render_video=False);sample={'station_id':'S000','base':[0,0,0],'level':'probe'}
            with patch('lastmile_dataflow.stations.execution.load_grasps',return_value={794:np.eye(4)}),patch('lastmile_dataflow.stations.execution.filter_station',return_value={'status':'valid'}),patch('lastmile_dataflow.stations.execution.PickMonitor',SyntheticFailureMonitor):
                path=run_station_attempt(scene,robot,cfg,CollectionConfig(output_dir=d),sample,'left',0,794,d,'failed',Budget(cfg),planner_factory=Planned)
            result=json.loads((path/'result.json').read_text());self.assertEqual(result['status'],'executed_failure')
            self.assertEqual(result['executed_steps'],1);self.assertTrue((path/'final_snapshot.npz').exists());self.assertTrue((path/'replay.npz').exists())
            self.assertTrue((path/'trajectory.jsonl').read_text())
    def test_planner_infrastructure_exception_is_not_grasp_failure(self):
        class Broken:
            def __init__(self,*args): raise RuntimeError('CUDA fixture failure')
        with tempfile.TemporaryDirectory() as d:
            sim,robot=make_sim(d); scene=Path(d)/'scene';sim.freeze(scene);sim.close()
            cfg=config(render_video=False); sample={'station_id':'S000','base':[0,0,0],'level':'probe'}
            with patch('lastmile_dataflow.stations.execution.load_grasps',return_value={794:np.eye(4)}),patch('lastmile_dataflow.stations.execution.filter_station',return_value={'status':'valid'}):
                path=run_station_attempt(scene,robot,cfg,CollectionConfig(output_dir=d),sample,'left',0,794,d,'broken',Budget(cfg),planner_factory=Broken)
            self.assertEqual(json.loads((path/'result.json').read_text())['status'],'infrastructure_error')
            self.assertEqual(json.loads((path/'layers.json').read_text())['execution'],'not_executed')


class VisionReviewTests(unittest.TestCase):
    def test_api_redirects_never_forward_credentials(self):
        import urllib.request
        req=urllib.request.Request('https://example.com/v1',headers={'Authorization':'Bearer fixture'})
        with self.assertRaises(RuntimeError):
            RejectRedirects().redirect_request(req,None,302,'redirect',{},'https://elsewhere.example/v1')
    def test_review_rejects_success_override_code_and_stale_id(self):
        self.assertEqual(parse_review('{"observation_id":"a","verdict":"unknown","reason":"看不清"}','a')['verdict'],'unknown')
        for raw in ('{}','{"observation_id":"old","verdict":"pass","reason":"yes"}',
                    '{"observation_id":"a","verdict":"pass","reason":"yes","task_success":true}'):
            with self.assertRaises(ValueError):parse_review(raw,'a')
    def test_env_only_three_keys_and_https(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'.env';path.write_text('LLM_API_KEY=test\nLLM_BASE_URL=https://example.com/v1\nLLM_MODEL=fixture\nOTHER_SECRET=ignore\n')
            self.assertEqual(set(read_settings(path)),{'LLM_API_KEY','LLM_BASE_URL','LLM_MODEL'})
            path.write_text('LLM_API_KEY=test\nLLM_BASE_URL=http://example.com\nLLM_MODEL=fixture\n')
            with self.assertRaises(ValueError):read_settings(path)


class EvidencePromotionTests(unittest.TestCase):
    def test_case_verified_cannot_be_human_assertion_without_evidence(self):
        from lastmile_dataflow.workflows.build import record_feedback
        with tempfile.TemporaryDirectory() as d:
            build=Path(d)/'builds'/'fixture'
            write_json(build/'task_candidate.json',{'build_id':'fixture','scene_version_id':'scene','target':'cup'})
            with self.assertRaises(ValueError):
                record_feedback(build,'case_verified',{'case_condition':{'status':'pass'}})
            self.assertFalse((Path(d)/'feedback').exists())
    def test_collection_manifest_rejects_added_or_modified_files(self):
        from lastmile_dataflow.exporting.collection import verify_manifest
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);write_json(run/'summary.json',{'fixture':True})
            write_json(run/'artifacts.json',{'files_sha256':{'summary.json':file_digest(run/'summary.json')}})
            verify_manifest(run)
            (run/'unexpected.txt').write_text('extra')
            with self.assertRaises(ValueError):verify_manifest(run)
            (run/'unexpected.txt').unlink();write_json(run/'summary.json',{'fixture':False})
            with self.assertRaises(ValueError):verify_manifest(run)


if __name__=='__main__':unittest.main()
