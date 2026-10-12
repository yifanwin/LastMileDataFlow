"""Capacity/eligibility regression; toy physics is not ProcTHOR delivery proof."""
import copy
import itertools
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import mujoco
import numpy as np

from lastmile_dataflow.construction.scene_eligibility import pick_type, allowed_case1_support, PICK_TYPES
from lastmile_dataflow.construction.scene_dataset_config import SceneDatasetRecipe
from lastmile_dataflow.construction.obstacle_config import ObstacleConfig
from lastmile_dataflow.construction.obstacle_sampling import case_assets, sample_surface_point
from lastmile_dataflow.construction.surface_capacity import probe_capacity, valid_certificate, surface_yaws
from lastmile_dataflow.scenes.geometry import extract_regions
from lastmile_dataflow.validation.obstacle_augmentation import case_construction_check, collision_pairs


def config(case='Case1', budget=1000):
    return ObstacleConfig('base','data','lib','target',schema_version='procedural-obstacles-v3',
        obstacle_count=[6,9] if case=='Case1' else [10,20],
        mandatory_categories=['Chair'] if case=='Case1' else [],ground_categories=['Chair'] if case=='Case1' else [],
        surface_categories=['Cup','Vase'],construction={'case':case,'rule_version':'scene-cases-v3',
        'allow_surface_capacity_shortfall':True,'capacity_probe_budget':budget})


class EligibilityTests(unittest.TestCase):
    def test_explicit_whitelist_and_dataset_aliases(self):
        pairs={'Cup':'cup','Mug':'mug','WineBottle':'wine bottle','SprayBottle':'atomizer',
               'Atomizer':'atomizer','SaltShaker':'salt shaker','PepperShaker':'pepper shaker',
               'SoapBottle':'soap dispenser','SoapDispenser':'soap dispenser'}
        for category, expected in pairs.items():
            self.assertEqual(pick_type({'category':category}),expected)
        for category in ['Bottle','SoapBar','Pencil','BaseballBat','Cupboard',None]:
            self.assertIsNone(pick_type({'category':category,'asset_id':'Cup_30'}))

    def test_support_restriction_is_semantic_not_name_substring(self):
        for category in ['CounterTop','DiningTable','SideTable','CoffeeTable','Desk','Table']:
            sim=SimpleNamespace(catalog=[{'mjcf_body':'s','category':category}])
            self.assertTrue(allowed_case1_support(sim,'s'))
        for category in ['Bed','TVStand','Dresser','Shelf',None]:
            sim=SimpleNamespace(catalog=[{'mjcf_body':'diningtable','category':category}])
            self.assertFalse(allowed_case1_support(sim,'diningtable'))

    def test_versioning_and_no_budget_as_capacity_switch(self):
        config()
        with self.assertRaises(ValueError):
            replace(config(),schema_version='procedural-obstacles-v2')
        with self.assertRaises(ValueError):
            replace(config(),construction={'case':'Case1','rule_version':'scene-cases-v2','allow_surface_capacity_shortfall':True})
        with self.assertRaises(ValueError):
            SceneDatasetRecipe('d','a','r',[103],rule_version='scene-cases-v3')
        r=SceneDatasetRecipe('d','a','r',[103],rule_version='scene-cases-v3',pick_target_types=list(PICK_TYPES),allow_surface_capacity_shortfall=True)
        self.assertTrue(r.allow_surface_capacity_shortfall)
        with self.assertRaises(ValueError):
            replace(r,pick_target_types=['book'])
        with self.assertRaises(ValueError):
            replace(r,case1_support_types=['Bed'])


class CapacityTests(unittest.TestCase):
    def setUp(self):
        xml='''<mujoco><worldbody>
          <body name="table" pos="0 0 .5"><geom name="top" type="box" size=".4 .4 .05"/></body>
          <body name="target" pos="0 0 .65"><freejoint/><geom type="box" size=".02 .02 .02"/></body>
          <body name="procedural_obstacle_0" pos="1 0 .1"><freejoint/><geom type="box" size=".05 .05 .05"/></body>
        </worldbody></mujoco>'''
        model=mujoco.MjModel.from_xml_string(xml);data=mujoco.MjData(model);mujoco.mj_forward(model,data)
        self.sim=SimpleNamespace(model=model,data=data,target_id=model.body('target').id,
            catalog=[{'mjcf_body':'table','category':'DiningTable'}])
        self.region=extract_regions(self.sim,'table')[0]
        self.points=[{'point_id':'p','xy':[.2,0.],'sources':[],'region':self.region}]
        local=np.array(list(itertools.product([-.02,.02],repeat=3)))
        self.assets=[{'asset_id':'small','category':'Cup','size':{'width':.04,'depth':.04,'height':.04},'local_points':local}]
        self.placements=[{'category':'Chair','kind':'ground','point_xy':[1,0]}]

    def test_exhaustive_failure_and_state_binding(self):
        ev,asset,p=probe_capacity(self.sim,self.placements,self.assets,self.points,config(),lambda p:False)
        self.assertEqual(ev['tested_poses'],12)
        self.assertEqual(ev['status'],'domain_exhausted')
        self.assertTrue(valid_certificate(self.sim,self.placements,ev))
        self.assertIsNone(asset)
        self.sim.data.qpos[0]+=.01
        self.assertFalse(valid_certificate(self.sim,self.placements,ev))

    def test_legal_minimum_asset_prevents_early_end(self):
        ev,asset,p=probe_capacity(self.sim,self.placements,self.assets,self.points,config(),lambda p:True)
        self.assertEqual(ev['status'],'legal_placement_found')
        self.assertEqual(asset['asset_id'],'small')
        self.assertIsNotNone(p)
        self.assertFalse(valid_certificate(self.sim,self.placements,ev))

    def test_larger_different_shape_is_also_checked(self):
        second=copy.deepcopy(self.assets[0]);second['asset_id']='large';second['size']['width']=.05
        calls=[]
        def collision(p):
            calls.append(p['asset_id']);return p['asset_id']=='large'
        ev,a,_=probe_capacity(self.sim,self.placements,self.assets+[second],self.points,config(),collision)
        self.assertEqual(calls[:12],['small']*12)
        self.assertEqual(a['asset_id'],'large')
        self.assertEqual(ev['status'],'legal_placement_found')

    def test_budget_is_inconclusive_not_exhausted(self):
        ev,_,_=probe_capacity(self.sim,self.placements,self.assets,self.points,config(budget=3),lambda p:False)
        self.assertEqual(ev['tested_poses'],3)
        self.assertEqual(ev['status'],'inconclusive')
        self.assertFalse(valid_certificate(self.sim,self.placements,ev))

    def test_missing_assets_are_not_a_capacity_certificate(self):
        ev,_,_=probe_capacity(self.sim,self.placements,[],self.points,config(),lambda p:False)
        self.assertFalse(valid_certificate(self.sim,self.placements,ev))

    def test_outside_surface_and_empty_pose_domain(self):
        pts=[{**self.points[0],'xy':[10,10]}]
        ev,_,_=probe_capacity(self.sim,self.placements,self.assets,pts,config(),lambda p:self.fail('outside surface must be rejected'))
        self.assertEqual(ev['rejection_counts']['full_footprint_outside_surface'],12)
        ev,_,_=probe_capacity(self.sim,self.placements,self.assets,[],config(),lambda p:False)
        self.assertTrue(valid_certificate(self.sim,self.placements,ev))

    def test_certificate_tamper_and_count_mismatch_rejected(self):
        ev,_,_=probe_capacity(self.sim,self.placements,self.assets,self.points,config(),lambda p:False)
        for field,value in [('tested_poses',11),('total_poses',11),('minimum_asset_id','unknown'),('exhaustive',False)]:
            self.assertFalse(valid_certificate(self.sim,self.placements,{**ev,field:value}))
        self.assertFalse(valid_certificate(self.sim,[],ev))

    def test_below_minimum_requires_valid_certificate_and_preserves_chair_rule(self):
        ev,_,_=probe_capacity(self.sim,self.placements,self.assets,self.points,config(),lambda p:False)
        supports=[{'body':'table'}]
        with patch('lastmile_dataflow.navigation.astar.task_support_obstacles',return_value=supports), \
             patch('lastmile_dataflow.construction.obstacle_regions.support_regions',return_value=[self.region]):
            self.assertFalse(case_construction_check(self.sim,self.placements,config())['valid'])
            self.assertTrue(case_construction_check(self.sim,self.placements,config(),capacity_evidence=ev)['valid'])
            self.sim.catalog[0]['category']='Bed'
            self.assertFalse(case_construction_check(self.sim,self.placements,config(),capacity_evidence=ev)['valid'])

    def test_minimum_fallback_not_rejected_by_average_area_heuristic(self):
        chair={'asset_id':'chair','category':'Chair','size':{'width':.3,'depth':.3,'height':.8}}
        geometry={'chair':chair,'small':self.assets[0]}
        result=case_assets(np.random.default_rng(3),6,{'ground':['Chair'],'surface':['Cup']},geometry,config().case_policy,['Chair'],surface_area_m2=.0001)
        self.assertEqual(len(result),6)

    def test_normal_yaw_is_in_certificate_domain(self):
        rng=np.random.default_rng(2);c=config();occupied=np.empty((0,2,3))
        for _ in range(20):
            point,yaw=sample_surface_point(rng,self.assets[0],self.points,[],occupied,c.case_policy,c.placement_gap_m)
            self.assertTrue(any(abs(yaw-a)<1e-12 for a in surface_yaws(self.region,c.case_policy)))

    def test_toy_real_narrowphase_not_aabb_capacity(self):
        # Callback uses MuJoCo narrowphase on actual boxes, not MOCK collision labels.
        def collision(proposal):
            x,y,z=proposal['pose']['position'];q=' '.join(map(str,proposal['pose']['quaternion_wxyz']))
            xml=f'''<mujoco><worldbody><body name="block" pos=".2 0 .573"><geom type="box" size=".04 .04 .04"/></body>
             <body name="procedural_obstacle_0" pos="{x} {y} {z}" quat="{q}"><freejoint/><geom type="box" size=".02 .02 .02"/></body></worldbody></mujoco>'''
            m=mujoco.MjModel.from_xml_string(xml);d=mujoco.MjData(m);mujoco.mj_forward(m,d)
            return collision_pairs(SimpleNamespace(model=m,data=d),[proposal],config())['valid']
        ev,_,_=probe_capacity(self.sim,[],self.assets,self.points,config(),collision)
        self.assertEqual(ev['status'],'domain_exhausted')
        self.assertEqual(ev['rejection_counts']['full_height_collision'],12)

class CapacityWorkflowTests(unittest.TestCase):
    def test_variant_accepts_capacity_limited_count_not_random_budget_failure(self):
        # MOCK workflow plumbing verifies early-stop control flow, not real scenes.
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock
        from contextlib import ExitStack
        from lastmile_dataflow.workflows.obstacle_augmentation import _variant
        from lastmile_dataflow.stations.no_edit_config import NoEditConfig
        model=mujoco.MjModel.from_xml_string('<mujoco><worldbody><body name="procedural_obstacle_0"><geom type="box" size=".1 .1 .1"/></body></worldbody></mujoco>')
        data=mujoco.MjData(model);mujoco.mj_forward(model,data)
        sim=SimpleNamespace(model=model,data=data,freeze=MagicMock(),close=MagicMock())
        session=MagicMock();session.candidate.return_value=sim;session.config=SimpleNamespace(graph_config=lambda:None)
        session.settle.return_value={'simulated_s':1}
        chair={'asset_id':'chair','category':'Chair','size':{'width':.2,'depth':.2,'height':.7}}
        cup={'asset_id':'cup','category':'Cup','size':{'width':.1,'depth':.1,'height':.1}}
        proposal={'asset_id':'chair','category':'Chair','kind':'ground','point_id':'p','point_xy':[0,0],
                  'yaw_rad':0.,'world_bounds':{'min':[-.1,-.1,0],'max':[.1,.1,.7]},'pose':{'position':[0,0,0],'quaternion_wxyz':[1,0,0,0]}}
        point={'point_id':'p','xy':[0,0],'sources':[{'kind':'station'}]}
        cert={'status':'domain_exhausted'}
        rows=[{'station_id':str(i),'geometry':'valid'} for i in range(5)]
        def media(*args,**kwargs):
            dest=Path(args[7]);dest.mkdir(parents=True)
            self.assertEqual(args[4],{'procedural_obstacle_0'})
            return {'images':[],'renderer_info':{}}
        module='lastmile_dataflow.workflows.obstacle_augmentation.'
        patches={'case_assets':lambda *a,**k:[chair]+[cup]*5,'surface_occupied_bounds':lambda *a:[],
                 'sample_placement':lambda *a,**k:(copy.deepcopy(proposal),None),
                 'sample_surface_point':lambda *a:(None,None),'collision_pairs':lambda *a,**k:{'valid':True},
                 '_capacity_probe':lambda *a:(cert,None,None),'clone_preparation':lambda *a:MagicMock(),
                 'ObstacleEditSession':lambda *a:MagicMock(),'matched_control':lambda *a:(sim,{}),
                 'validate_augmented_scene':lambda *a,**k:{'valid':True},'build_scene_graph':lambda *a,**k:{},
                 'render_obstacle_pair':media,'evaluate_stations':lambda *a,**k:rows,
                 'augmented_grid':lambda *a:SimpleNamespace(free=np.ones((2,2),bool)),
                 'connectivity':lambda *a:{'free_cells':4},'save_grid':lambda *a:None,'region_plot':lambda *a:None}
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name,fn in patches.items():stack.enter_context(patch(module+name,side_effect=fn))
            stack.enter_context(patch('lastmile_dataflow.stations.scene_package.select_stations',return_value=(rows,{})))
            # Nonempty placeholder surface domain keeps the empty-object guard distinct.
            surfaces=[{'xy':[0,0], 'region':SimpleNamespace(region_id='s',bounds=[0,1,0,1])}]
            task={'target_body':'target','anchor_world':[0,0,.5],'support_obstacles':[{'body':'table','min':[0,0,0],'max':[1,1,1]}]}
            result=_variant(session,sim,task,replace(config(),obstacle_count=[6,6],max_candidates=100),NoEditConfig(),
                            {'ground':['Chair'],'surface':['Cup']},{'chair':chair,'cup':cup},{'points':[point]},surfaces,
                            rows,.1,Path(directory)/'variant','v000',1,set())
            self.assertEqual(result['status'],'accepted')
            self.assertEqual(result['actual_surface_count'],0)
            self.assertTrue(result['capacity_shortfall'])
            self.assertTrue((Path(directory)/'variant/capacity.json').is_file())

    def test_capacity_fallback_cannot_replace_required_case2_large_item(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock
        from lastmile_dataflow.workflows.obstacle_augmentation import _variant
        from lastmile_dataflow.stations.no_edit_config import NoEditConfig
        large={'asset_id':'large','category':'Vase','size':{'width':.3,'depth':.2,'height':.3}}
        surface={'xy':[0,0],'region':SimpleNamespace(region_id='s',bounds=[0,1,0,1])}
        rows=[{'geometry':'valid'}]*5
        module='lastmile_dataflow.workflows.obstacle_augmentation.'
        with tempfile.TemporaryDirectory() as directory, \
             patch(module+'case_assets',return_value=[large]*10), \
             patch(module+'surface_occupied_bounds',return_value=[]), \
             patch(module+'sample_surface_point',return_value=(None,None)), \
             patch(module+'_capacity_probe',side_effect=AssertionError('required large asset must not be replaced by minimum cup')):
            task={'anchor_world':[0,0,.5],'support_obstacles':[{'min':[0,0,0],'max':[1,1,1]}]}
            result=_variant(MagicMock(),None,task,replace(config('Case2'),max_candidates=100),NoEditConfig(),
                {'ground':[],'surface':['Vase','Cup']},{'large':large},{'points':[]},[surface],rows,.1,
                Path(directory)/'variant','v000',1,set())
            self.assertEqual(result['status'],'no_feasible_placement')
            self.assertEqual(result['physical_trials'],0)
