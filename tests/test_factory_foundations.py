import copy
from pathlib import Path
import tempfile
import unittest
import numpy as np

from lastmile_dataflow.io import write_json


class GraspTests(unittest.TestCase):
    def test_antipodal_limits_and_seed(self):
        from lastmile_dataflow.grasping.sampling import antipodal_grasps
        p = np.array([[-.02,0,0],[.02,0,0],[0,-.02,0],[0,.02,0]])
        n = p / .02
        a = antipodal_grasps(p,n,max_opening_m=.08,seed=3,max_candidates=12)
        b = antipodal_grasps(p,n,max_opening_m=.08,seed=3,max_candidates=12)
        self.assertEqual(a,b); self.assertTrue(a)
        for g in a:
            r = np.array(g['transform'])[:3,:3]
            np.testing.assert_allclose(r.T@r,np.eye(3),atol=1e-8)
            self.assertAlmostEqual(np.linalg.det(r),1.)
            self.assertLess(g['width_m'],.08)
        self.assertEqual(antipodal_grasps(p,n,max_opening_m=.01),[])

    def test_cache_cannot_hide_wrong_robot_or_unverified(self):
        from lastmile_dataflow.grasping.cache import save_grasp_cache, load_grasp_cache
        rows=[{'transform':np.eye(4).tolist(),'width_m':.04,'score':.5,'region':'body','simulation_pass':False}]
        with tempfile.TemporaryDirectory() as d:
            p=save_grasp_cache(Path(d)/'cache',asset_id='cup',asset_digest='a'*64,
                              gripper_digest='b'*64,candidates=rows)
            with self.assertRaises(ValueError): load_grasp_cache(p,gripper_digest='b'*64,require_verified=True)
            with self.assertRaises(ValueError): load_grasp_cache(p,gripper_digest='c'*64,require_verified=False)
            packet, selected=load_grasp_cache(p,gripper_digest='b'*64,require_verified=False)
            self.assertEqual(len(selected),1)
            with self.assertRaises(FileExistsError):
                save_grasp_cache(Path(d)/'cache',asset_id='cup',asset_digest='a'*64,
                                 gripper_digest='b'*64,candidates=rows)


class EdgeTests(unittest.TestCase):
    def test_rotated_support_edges_and_sampling(self):
        from lastmile_dataflow.scenes.geometry import SupportRegion
        from lastmile_dataflow.stations.sampling import edge_samples
        r=SupportRegion('r','table','g','plane',[3,4,1],[[0,-1,0],[1,0,0],[0,0,1]],[-1,1,-.5,.5],1.,'fixture')
        rows=edge_samples(r,[3,4,1.1],base_radius_m=.3,edge_gap_m=.1,spacing_m=.5)
        self.assertEqual({s['edge'] for s in rows},{'u-','u+','v-','v+'})
        self.assertEqual(rows,edge_samples(r,[3,4,1.1],base_radius_m=.3,edge_gap_m=.1,spacing_m=.5))
        for row in rows:
            x,y,yaw=row['base']; self.assertAlmostEqual(yaw,np.arctan2(4-y,3-x))
            self.assertAlmostEqual(row['edge_gap_m'],.1)

    def test_unknown_blocks_start_selection(self):
        from lastmile_dataflow.stations.start_selection import start_pairs
        from lastmile_dataflow.construction.case_spec import load_case_spec
        from test_case_factory import station
        root=Path(__file__).resolve().parents[1]
        spec=load_case_spec(root/'configs/case_specs/case1.json')
        rows=[station('S0','u-',[0,0,0]),station('S1','u+',[1,0,3.14],'success','success'),
              station('S2','u+',[1,.1,3.14],'success','success')]
        self.assertEqual(len(start_pairs(spec,rows)),2)
        rows.append(station('S3','u-',[0,.1,0],reach='unknown'))
        self.assertEqual(start_pairs(spec,rows),[])




class NativeBoundaryTests(unittest.TestCase):
    @staticmethod
    def synthetic_gripper():
        import trimesh
        def box(center,extent):
            mesh=trimesh.creation.box(extents=extent)
            return {'vertices':(mesh.vertices+center).tolist(),'faces':mesh.faces.tolist()}
        finger={'damping':50.,'armature':10.,'frictionloss':0.,'gravcomp':1.,'mass':.0333,
                'inertia':[1.2e-5,1.1e-5,2e-6],'inertia_quat':[1,0,0,0]}
        gripper={'groups':[{'name':'palm','meshes':[box([0,0,-.08],[.04,.12,.02])]},
                 dict(finger,name='finger1',axis=[0,-1,0],range=[-.05,0],com=[0,.002,0],meshes=[box([0,.002,0],[.02,.004,.06])]),
                 dict(finger,name='finger2',axis=[0,-1,0],range=[0,.05],com=[0,-.002,0],meshes=[box([0,-.002,0],[.02,.004,.06])])],
                 'actuator':{'joint':'finger1','kp':4000.,'kv':400.,'ctrlrange':[-.05,0.],'forcerange':None},
                 'coupling':{'joint1':'finger1','joint2':'finger2','polycoef':[0,-1,0,0,0],'solref':[.02,1],'solimp':[.9,.95,.001,.5,2]},
                 'options':{'timestep':.004,'cone':1,'impratio':10.,'integrator':0,'iterations':100,'noslip_iterations':4,'multiccd':True},
                 'excluded_pairs':[['palm','finger1'],['palm','finger2']]}
        return gripper,box

    def test_isolated_gripper_each_candidate_fresh(self):
        from lastmile_dataflow.runtime.grasp_filter import filter_grasps
        gripper,box=self.synthetic_gripper()
        obj=[box([0,0,0],[.025,.025,.025])]
        pose=np.eye(4); pose[2,3]=-.2
        candidates=[{'transform':pose.tolist(),'width_m':.025,'score':1.,'region':'body','simulation_pass':False}]*2
        a=filter_grasps(gripper,obj,candidates)
        self.assertEqual(a[0],a[1]); self.assertFalse(a[0]['simulation_pass'])

    def test_isolated_gripper_positive_control_lifts_box(self):
        # A centred top-down pinch on a narrow box must pass; otherwise the filter itself is broken.
        # Narrow (12 mm) keeps the unlimited native position-servo squeeze near the phase-3 cup grasp (~20 N).
        from lastmile_dataflow.runtime.grasp_filter import filter_grasps
        gripper,box=self.synthetic_gripper()
        obj=[box([0,0,0],[.04,.012,.04])]
        pose=np.eye(4); pose[:3,:3]=np.array([[-1,0,0],[0,1,0],[0,0,-1]]).T; pose[2,3]=.01
        row=filter_grasps(gripper,obj,[{'transform':pose.tolist(),'width_m':.04,'score':1.,'region':'body','simulation_pass':False}],
                          object_inertial={'mass':.05,'pos':[0,0,0],'quat':[1,0,0,0],'inertia':[1.3e-5]*3})[0]
        self.assertTrue(row['simulation_pass'],row['simulation_evidence'])

    def test_isolated_model_excludes_palm_finger_contacts(self):
        from lastmile_dataflow.runtime.grasp_filter import isolated_model
        import mujoco
        gripper,box=self.synthetic_gripper()
        model=isolated_model(gripper,[box([0,0,0],[.02,.02,.02])],.05)
        pairs={tuple(sorted((model.body(int(s>>16)).name,model.body(int(s&0xFFFF)).name))) for s in model.exclude_signature}
        self.assertEqual(pairs,{('finger1','palm'),('finger2','palm')})
        self.assertEqual(model.nu,1); self.assertEqual(model.neq,2)  # finger coupling + hand weld
        self.assertEqual(model.nmocap,1); self.assertEqual(model.body('tcp').mocapid,-1)

    def test_pinch_sampler_feeds_filter_for_small_box(self):
        from lastmile_dataflow.grasping.sampling import pinch_grasps,finger_geometry
        from lastmile_dataflow.runtime.grasp_filter import filter_grasps
        import trimesh
        gripper,box=self.synthetic_gripper()
        obj=[box([0,0,0],[.04,.012,.04])]
        mesh=trimesh.Trimesh(obj[0]['vertices'],obj[0]['faces']); points,faces=trimesh.sample.sample_surface(mesh,2000,seed=0)
        fingers=finger_geometry(gripper)
        rows=pinch_grasps(points,mesh.face_normals[faces],support_up=[0,0,1],max_opening_m=.1,fingers=fingers,max_candidates=12)
        self.assertTrue(rows)
        for r in rows:
            pose=np.array(r['transform'])
            self.assertTrue(np.allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-9))
            self.assertLessEqual(np.dot(pose[:3,2],[0,0,1]),1e-9)  # never approaches from below the support
        passed=filter_grasps(gripper,obj,rows,object_inertial={'mass':.05,'pos':[0,0,0],'quat':[1,0,0,0],'inertia':[1e-5]*3})
        self.assertTrue(any(r['simulation_pass'] for r in passed),[r['filter_reason'] for r in passed])

    def test_pinch_sampler_rejects_solid_object_wider_than_opening(self):
        from lastmile_dataflow.grasping.sampling import pinch_grasps,finger_geometry
        import trimesh
        gripper,_=self.synthetic_gripper()
        mesh=trimesh.creation.box(extents=[.15,.15,.15]); points,faces=trimesh.sample.sample_surface(mesh,int(mesh.area/.003**2),seed=0)  # same ~3 mm spacing as generation
        self.assertEqual(pinch_grasps(points,mesh.face_normals[faces],support_up=[0,0,1],max_opening_m=.1,fingers=finger_geometry(gripper)),[])

    def test_displacement_gate_ignores_contact_chatter_but_not_sliding(self):
        from lastmile_dataflow.runtime.preparation import SettleConfig,body_stable,velocity_jitter,scoped_stability
        strict=SettleConfig(); relaxed=SettleConfig(window_s=1.,require_source_stability=False,gate_on_velocity=False)
        chatter={'speed_m_s':6.17,'angular_speed_rad_s':.69,'drift_m':.00019,'rotation_rad':.0129}  # val-103 spoon
        sliding={'speed_m_s':.01,'angular_speed_rad_s':.0,'drift_m':.01,'rotation_rad':.0}
        self.assertFalse(body_stable(chatter,strict)); self.assertTrue(body_stable(chatter,relaxed))
        self.assertTrue(velocity_jitter(chatter,relaxed)); self.assertFalse(body_stable(sliding,relaxed))
        settling={'metrics':{'spoon':chatter,'cup':{'speed_m_s':0.,'angular_speed_rad_s':0.,'drift_m':0.,'rotation_rad':0.},'pot':sliding}}
        self.assertTrue(scoped_stability(settling,relaxed,['cup','spoon'])['stable'])
        result=scoped_stability(settling,relaxed,['cup','pot','ghost'])
        self.assertFalse(result['stable']); self.assertEqual(set(result['unstable']),{'pot'}); self.assertEqual(result['missing_metrics'],['ghost'])

    def test_task_scope_is_target_neighbourhood_on_same_support(self):
        from lastmile_dataflow.workflows.factory_backend import task_scope
        from types import SimpleNamespace
        node=lambda x,motion='free':{'pose':{'position':[x,0,.8]},'root_motion':motion}
        graph=SimpleNamespace(nodes={'cup':node(0),'near':node(.3),'far':node(.9),'other':node(.1),'table':node(0,'fixed'),'shelf':node(0,'fixed')},
            edges=[{'predicate':'supported_by','args':[a,b]} for a,b in (('cup','table'),('near','table'),('far','table'),('other','shelf'))])
        self.assertEqual(task_scope(graph,{'target':'cup','support':'table'}),{'cup','near'})

    def test_asset_identity_ignores_instance_names(self):
        from lastmile_dataflow.grasping.geometry import asset_collision_digest
        a=[{'vertices':[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]],'faces':[[0,1,2]],'source_geom':'instance1'}]
        b=copy.deepcopy(a); b[0]['source_geom']='instance2'; b[0]['vertices'][0][0]+=1e-9
        self.assertEqual(asset_collision_digest(a),asset_collision_digest(b))

    def test_factory_config_unknown_and_bool_budgets(self):
        from lastmile_dataflow.construction.factory_config import load_factory_config
        from lastmile_dataflow.io import read_json
        raw=read_json(Path(__file__).resolve().parents[1]/'configs/case_factory/default.json')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            for k,v in [('seed',True),('unexpected',1),('spacing_m',float('nan'))]:
                data=copy.deepcopy(raw); data[k]=v; write_json(path,data) if k!='spacing_m' else path.write_text(__import__('json').dumps(data))
                with self.assertRaises(ValueError): load_factory_config(path)


class NativeEvaluatorTests(unittest.TestCase):
    def test_complete_plan_is_not_execution_and_infrastructure_not_no_solution(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from helpers import make_sim
        from lastmile_dataflow.config import CollectionConfig
        from lastmile_dataflow.workflows.factory_backend import NativeFactoryBackend
        from lastmile_dataflow.stations.config import StationConfig
        from lastmile_dataflow.integrations.waypoints import PlanResult
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); sim,robot=make_sim(root); snapshot=root/'snapshot'; sim.freeze(snapshot); sim.close()
            backend=NativeFactoryBackend.__new__(NativeFactoryBackend)
            backend.robot=robot; backend.collection=CollectionConfig(record_video=False); backend.config={}
            backend.station_config=StationConfig(target='target',asset_id='fixture',grasp_path='/fixture-only.npz',
                grasp_sha256='a'*64,robot_planner_dir='/fixture-only',grasp_ids=[0],render_video=False)
            candidate={'snapshot':str(snapshot),'target':'target'}
            sample={'station_id':'S0','edge':'u-','base':[0,0,0],'edge_gap_m':.1,'level':'edge'}
            class Reach:
                def __init__(self,*args): pass
                def solve(self,*args): return 'success'
            class Planner:
                def __init__(self,sim,config,side,path): self.names=tuple(sim.robot.groups[side+'_arm']); self.q=tuple(sim.robot.group(side+'_arm'))
                def plan(self,goal): return PlanResult('success',self.names,(self.q,),{})
            class BrokenPlanner(Planner):
                def plan(self,goal): return PlanResult('infrastructure_error',self.names,(),{})
            with patch('lastmile_dataflow.workflows.factory_backend.NativeReach',Reach), \
                 patch('lastmile_dataflow.workflows.factory_backend.head_frame',return_value=(None,None,{'visible_pixels':100})), \
                 patch('lastmile_dataflow.planning.curobo.load_grasps',return_value={0:np.eye(4)}):
                with patch('lastmile_dataflow.workflows.factory_backend.NativePlanner',Planner):
                    row=backend.evaluate(candidate,sample,root/'probe1')
                    self.assertEqual(row['plan'],'success'); self.assertEqual(row['execution'],'not_executed')
                    from lastmile_dataflow.io import read_json
                    trials=read_json(root/'probe1/probe.json')['trials']
                    self.assertEqual([r['phase'] for r in trials[-1]['segments']],['pregrasp','approach','lift','retreat'])
                with patch('lastmile_dataflow.workflows.factory_backend.NativePlanner',BrokenPlanner):
                    row=backend.evaluate(candidate,sample,root/'probe2')
                    self.assertEqual(row['plan'],'infrastructure_error')

    def test_symlink_resource_root_and_generation_failure_preserved(self):
        from unittest.mock import patch
        from lastmile_dataflow.workflows.grasp_generation import run_grasp_generation
        from lastmile_dataflow.io import read_json
        from lastmile_dataflow.config import RobotConfig
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); raw=root/'raw'; raw.mkdir(); linked=root/'assets'; linked.mkdir()
            (raw/'source.xml').write_text('<mujoco/>'); (linked/'source.xml').symlink_to(raw/'source.xml')
            with patch('lastmile_dataflow.runtime.simulation.Simulation.from_source',side_effect=RuntimeError('fixture_loading_error')) as mocked:
                path=run_grasp_generation(linked/'source.xml',root/'meta.json','target',RobotConfig('fixture'),{},root/'new-run')
                self.assertEqual(mocked.call_args.args[0].xml_path,str(linked/'source.xml'))
            self.assertEqual(read_json(path/'result.json')['status'],'infrastructure_or_generation_error')
            with self.assertRaises(FileExistsError):
                run_grasp_generation(linked/'source.xml',root/'meta.json','target',RobotConfig('fixture'),{},root/'new-run')


class GuidedSamplingTests(unittest.TestCase):
    def test_support_up_preserves_downward_approach_and_horizontal_closing(self):
        from lastmile_dataflow.grasping.sampling import antipodal_grasps
        p=np.array([[-.02,0,0],[.02,0,0],[0,-.02,0],[0,.02,0]])
        rows=antipodal_grasps(p,p/.02,max_opening_m=.08,support_up=[0,0,1],max_candidates=16)
        self.assertTrue(rows)
        for row in rows:
            pose=np.array(row['transform']); self.assertLess(pose[2,2],-.9);self.assertLess(abs(pose[2,1]),.15)
        with self.assertRaises(ValueError): antipodal_grasps(p,p/.02,max_opening_m=.08,support_up=[0,0,0])

    def test_index_retrieval_is_bounded_and_deterministic(self):
        from lastmile_dataflow.catalog.factory_index import create_geometry_index,query_geometry_index
        from lastmile_dataflow.construction.case_spec import load_case_spec
        spec=load_case_spec(Path(__file__).resolve().parents[1]/'configs/case_specs/case1.json')
        rows=[{'candidate_id':str(i),'scene_id':str(i%2),'category':'Cup','support':'table'} for i in range(6)]
        with tempfile.TemporaryDirectory() as d:
            p=create_geometry_index(rows,Path(d)/'index.sqlite')
            a=query_geometry_index(p,spec,limit=4,seed=3); b=query_geometry_index(p,spec,limit=4,seed=3)
            self.assertEqual(a,b);self.assertEqual(len(a),4)
            with self.assertRaises(FileExistsError): create_geometry_index(rows,p)


if __name__=='__main__': unittest.main()
