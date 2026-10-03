"""Synthetic protocol regression; not real RBY-1 manipulation evidence."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
from helpers import fixtures
from lastmile_dataflow.config import CollectionConfig, TaskConfig
from lastmile_dataflow.construction.config import BuildConfig, BuildProtocol, BuildBudget, load_build_config
from lastmile_dataflow.runtime.build_session import BuildSession
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.io import write_json, read_json, file_digest
from lastmile_dataflow.scenes.geometry import placement_pose
from lastmile_dataflow.agents.protocol import observe, parse_decision, DecisionGateway
from lastmile_dataflow.catalog.index import create_index,search_index
from lastmile_dataflow.construction.candidates import candidates
from lastmile_dataflow.workflows.build import run_build, record_feedback, RequestBudget, build_request


def tabletop(root, variant=0):
    source,robot=fixtures(root)
    # `barrier` is a floor-level obstacle on one side only: case1.5's clearance difference must come
    # from a real obstruction at standing height, not from a tabletop object's bounding sphere.
    text=f'''<mujoco><compiler angle="radian"/><option integrator="implicitfast"/>
    <worldbody><geom name="floor" type="plane" size="0 0 .01"/>
    <body name="table" pos="2 0 .4"><geom name="top" type="box" size=".5 .4 .04"/></body>
    <body name="barrier" pos="2 .95 .30"><geom name="barrier_geom" type="box" size=".5 .05 .30"/></body>
    <body name="target" pos="{2+variant*.03} 0 .50"><freejoint name="target_free"/><geom name="target_geom" type="box" size=".04 .04 .05" mass=".1"/></body>
    <body name="neighbor" pos="2.35 .20 .49"><freejoint name="neighbor_free"/><geom name="neighbor_geom" type="box" size=".02 .02 .05" mass=".1"/></body>
    </worldbody></mujoco>'''
    Path(source.xml_path).write_text(text)
    config=BuildConfig('2.0','synthetic-case1','case1','target','table',robot_base=[0,0,0],editable=['target'],
                       parameters={'distance_range_m':[1.4,2.6]},protocol=BuildProtocol(settle_s=.5,max_settle_s=1,window_s=.2))
    collection=CollectionConfig(output_dir=str(Path(root)/'outputs'),max_steps=2,record_video=False)
    return source,robot,config,collection


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.source,self.robot,self.config,self.collection=tabletop(self.root)
        self.session=BuildSession(self.source,self.robot,self.config,self.collection)
    def tearDown(self):
        self.session.close(); self.tmp.cleanup()
    def stable(self):
        self.session.settle(); self.assertTrue(self.session.validate()['valid'],self.session.last_check)
    def move(self,x=2.15,y=0):
        b=self.session.sim.model.body('target').id; region=self.session.placements['target']
        pose=placement_pose(self.session.sim,b,region,[x-2,y],0)
        return {'op':'move','instance':'target','pose':pose,'region_id':region.region_id}
    def test_zero_edit_freeze_independent(self):
        self.stable(); state=self.session.sim.state_vector().copy(); cp=self.session.checkpoint_id
        version=self.session.freeze(self.root/'frozen',build_id='zero')
        with self.assertRaises(RuntimeError): self.session.transact([self.move()],revision=0)
        sim=Simulation.from_snapshot(self.root/'frozen',self.robot,target='target')
        try:
            np.testing.assert_array_equal(state,sim.state_vector()); self.assertEqual(cp,version['checkpoint_id'])
            sim.begin()
            with self.assertRaises(RuntimeError): sim.restore_snapshot(self.root/'frozen/initial.npz')
        finally: sim.close()
    def test_move_rotation_undo_identity_and_source_unchanged(self):
        self.stable(); source_hash=file_digest(self.source.xml_path); before=self.session.checkpoint_id
        entry=self.session.transact([self.move()],revision=self.session.revision)
        self.assertEqual(entry['status'],'committed',entry); after=self.session.checkpoint_id
        self.assertNotEqual(before,after)
        self.session.undo(); self.assertEqual(before,self.session.checkpoint_id)
        self.assertEqual(source_hash,file_digest(self.source.xml_path))
        self.session.settle(); pose=np.r_[self.session.sim.data.xpos[self.session.sim.model.body('target').id],[np.cos(.3),0,0,np.sin(.3)]].tolist()
        entry=self.session.transact([{'op':'rotate','instance':'target','pose':pose}],revision=self.session.revision)
        self.assertEqual(entry['status'],'committed',entry)
    def test_failed_placement_rollback_preserves_exact_checkpoint(self):
        self.stable(); before=self.session.checkpoint_id
        entry=self.session.transact([{'op':'move','instance':'target','pose':[4,0,.2,1,0,0,0]}],revision=self.session.revision)
        self.assertEqual(entry['status'],'rolled_back'); self.assertEqual(before,self.session.checkpoint_id)
        self.assertIn('wrong_support_or_region',[i['code'] for i in entry['check']['issues']])
    def test_penetration_and_wrong_support_stationary(self):
        self.stable()
        q=self.session.sim.model.joint('target_free').qposadr[0]
        for pose in ([2,0,.42,1,0,0,0],[4,0,.05,1,0,0,0],[2.35,.2,.58,1,0,0,0]):
            self.session.sim.data.qpos[q:q+7]=pose; self.session.sim.data.qvel[:]=0
            mujoco.mj_forward(self.session.sim.model,self.session.sim.data)
            check=self.session.validate(); self.assertFalse(check['valid'])
    def test_delete_support_with_dependents_rejected(self):
        self.stable(); self.session.config=replace(self.config,editable=['target','table'],allowed_operations=['move','delete'])
        cp=self.session.checkpoint_id
        entry=self.session.transact([{'op':'delete','instance':'table'}],revision=self.session.revision)
        self.assertEqual(entry['status'],'rolled_back');self.assertIn('dependents',entry['error']); self.assertEqual(cp,self.session.checkpoint_id)
    def test_stale_and_protected_permission(self):
        self.stable(); cp=self.session.checkpoint_id
        for ops,rev in [([self.move()],999),([{'op':'delete','instance':'neighbor'}],self.session.revision)]:
            entry=self.session.transact(ops,revision=rev);self.assertEqual(entry['status'],'rolled_back');self.assertEqual(cp,self.session.checkpoint_id)
    def test_continuous_execution_cannot_edit(self):
        checkpoint=self.session.snapshot()
        self.session.sim.begin()
        with self.assertRaises(RuntimeError): self.session.restore(checkpoint)
        with self.assertRaises(RuntimeError): self.session.snapshot()
        with self.assertRaises(RuntimeError): self.session.settle()
        with self.assertRaises(RuntimeError): self.session.freeze(self.root/'bad',build_id='bad')
    def test_neighbor_side_effect_blocks(self):
        self.stable(); checkpoint=self.session.snapshot(); q=self.session.sim.model.joint('neighbor_free').qposadr[0]
        self.session.sim.data.qpos[q]+=1; mujoco.mj_forward(self.session.sim.model,self.session.sim.data)
        check=self.session.validate();self.assertFalse(check['valid']);self.assertIn('protected_or_neighbor_changed',[x['code'] for x in check['issues']])
        self.session.restore(checkpoint);self.session.settle()
        self.session.config=replace(self.config,editable=['target','neighbor'])
        entry=self.session.transact([{'op':'move','instance':'neighbor','pose':[4,0,.05,1,0,0,0]}],revision=self.session.revision)
        self.assertEqual(entry['status'],'rolled_back')
        self.assertIn('wrong_support_or_region',[x['code'] for x in entry['check']['issues']])
    def test_stability_window_and_flight_blocked(self):
        self.stable(); self.session.history=[]
        self.assertFalse(self.session.validate()['valid'])
    def test_case2_annotation_and_requirements_refresh(self):
        p={'handle':{'body':'target','axis_local':[0,1,0],'verified':True,'source':'synthetic_annotation'},'desired_direction_world':[0,1,0]}
        self.session.config=replace(self.config,case_type='case2',parameters=p)
        self.stable(); pose=self.move()['pose'];pose[:3]=self.session.sim.data.xpos[self.session.sim.model.body('target').id].tolist(); pose[3:]=[0,0,0,1]
        before=self.session.checkpoint_id
        entry=self.session.transact([{'op':'rotate','instance':'target','pose':pose}],revision=self.session.revision)
        self.assertEqual(entry['status'],'rolled_back'); self.assertEqual(before,self.session.checkpoint_id)
        self.assertEqual(entry['check']['requirements'][0]['status'],'fail')
        with self.assertRaises(ValueError): replace(self.config,case_type='case2',parameters={**p,'handle':{**p['handle'],'verified':False}})
    def make_asset(self):
        path=self.root/'asset.xml';path.write_text('<mujoco><worldbody><body name="block"><freejoint name="free"/><geom type="box" size=".025 .025 .04" mass=".05"/></body></worldbody></mujoco>')
        pool=self.root/'pool.json'
        write_json(pool,{'schema_version':'2.0','assets':[{'asset_id':'block','xml_path':'asset.xml','root_body':'block','sha256':file_digest(path),
                'qualification':{'load':{'status':'verified','evidence':'synthetic_compile'},'placement':{'status':'verified','evidence':'synthetic_test'},'grasp':{'status':'unknown'},'handle_grasp':{'status':'unknown'}},'parts':{}}]})
        from lastmile_dataflow.catalog.assets import AssetPool
        self.session.pool=AssetPool(pool)
        self.session.config=replace(self.config,editable=['target','obstacle'],allowed_operations=['move','rotate','add','delete'])
    def test_add_delete_topology_undo_migration(self):
        self.stable(); self.make_asset(); cp=self.session.checkpoint_id; mid=self.session.model_id
        robot_before=self.session.sim.robot.state()
        add={'op':'add','instance':'obstacle','asset_id':'block','pose':[1.8,.2,.483,1,0,0,0]}
        entry=self.session.transact([add],revision=self.session.revision)
        self.assertEqual(entry['status'],'committed',entry);self.assertNotEqual(mid,self.session.model_id)
        self.assertIn('obstacle',[x['instance_id'] for x in self.session.sim.catalog])
        for name in robot_before: np.testing.assert_allclose(robot_before[name],self.session.sim.robot.state()[name],atol=1e-7)
        self.session.undo(); self.assertEqual(mid,self.session.model_id);self.assertEqual(cp,self.session.checkpoint_id)
        entry=self.session.transact([add],revision=self.session.revision);self.assertEqual(entry['status'],'committed',entry)
        entry=self.session.transact([{'op':'delete','instance':'obstacle'}],revision=self.session.revision);self.assertEqual(entry['status'],'committed',entry)
        self.session.undo(); self.session.sim.model.body('obstacle')
    def test_asset_missing_resources_collision_and_cross_structure_failure(self):
        self.stable(); self.make_asset(); cp=self.session.checkpoint_id
        for add in ({'op':'add','instance':'target','asset_id':'block','pose':[1.8,.2,.483,1,0,0,0]},
                    {'op':'add','instance':'obstacle','asset_id':'bad','pose':[1.8,.2,.483,1,0,0,0]},
                    {'op':'add','instance':'obstacle','asset_id':'block','pose':[4,.2,.483,1,0,0,0]}):
            entry=self.session.transact([add],revision=self.session.revision)
            self.assertEqual(entry['status'],'rolled_back',entry);self.assertEqual(cp,self.session.checkpoint_id)
        (self.root/'asset.xml').unlink()
        entry=self.session.transact([{'op':'add','instance':'obstacle','asset_id':'block','pose':[1.8,.2,.483,1,0,0,0]}],revision=self.session.revision)
        self.assertEqual(entry['status'],'rolled_back')
    def test_static_model_move_rebuild_and_undo(self):
        self.stable(); self.session.config=replace(self.config,editable=['target','table','neighbor'],allowed_operations=['move'])
        before=self.session.model_id
        # Dependency-group operations must put both table and target in new valid placement.
        region=self.session.placements['target']; region_pose=[2.1,0,.4,1,0,0,0]
        move=self.move(2.1)
        entry=self.session.transact([{'op':'move','instance':'table','pose':region_pose},move,{'op':'move','instance':'neighbor','pose':[2.45,.2,.493,1,0,0,0]}],revision=self.session.revision)
        self.assertEqual(entry['status'],'committed',entry); self.assertNotEqual(before,self.session.model_id)
        self.session.undo();self.assertEqual(before,self.session.model_id)
    def test_snapshot_corruption_v2_and_mismatch(self):
        self.stable(); self.session.freeze(self.root/'frozen',build_id='snap')
        p=self.root/'frozen/initial.npz';p.write_bytes(b'broken')
        with self.assertRaises(ValueError):Simulation.from_snapshot(self.root/'frozen',self.robot)
    def test_index_and_workflow_feedback(self):
        create_index([self.source],self.root/'index.sqlite')
        hits=search_index(self.root/'index.sqlite',dynamic=True);self.assertEqual(len(hits),2)
        path=run_build(self.source,self.robot,self.config,self.collection,images=False,regression=False,build_id='workflow')
        result=read_json(path/'result.json');self.assertEqual(result['status'],'candidate_ready_regression_pending',result)
        self.assertEqual(result['results']['case_condition']['status'],'unknown')
        frozen=Path(result['frozen_scene_dir']);old=file_digest(frozen/'version.json')
        feedback=record_feedback(path,'valid_unsolved',{'test_only':True})
        self.assertEqual(read_json(feedback)['action'],'retain_unsolved_do_not_remove_obstacles');self.assertEqual(old,file_digest(frozen/'version.json'))
        for classification in ('scene_invalid','success_without_expected_difficulty','infrastructure_failure'):
            f=record_feedback(path,classification,{'test_only':True},new_build_id='new-candidate')
            self.assertEqual(read_json(f)['classification'],classification)
            self.assertEqual(old,file_digest(frozen/'version.json'))
    def test_protocol_strict_and_timeout(self):
        self.stable();cs=candidates(self.session);packet=observe(self.session,cs,self.root/'obs')
        base={'action':'select','revision':self.session.revision,'observation_id':packet['observation_id'],'candidate_id':cs[0]['candidate_id']}
        self.assertEqual(parse_decision(base,packet),base)
        for v in ('{', {**base,'revision':999},{**base,'candidate_id':'missing'},{**base,'execute':'print(1)'},{**base,'action':'modify_threshold'}):
            with self.assertRaises(ValueError):parse_decision(v,packet)
        gateway=DecisionGateway(lambda o:base,budget=1);gateway.failed.add(base['candidate_id'])
        with self.assertRaises(ValueError):gateway.decide(packet)
        with self.assertRaises(RuntimeError):gateway.decide(packet)
        gateway=DecisionGateway(lambda o:time.sleep(.1),budget=1,timeout_s=.005)
        with self.assertRaises(TimeoutError):gateway.decide(packet)
    def test_all_four_rule_templates_and_two_source_layouts(self):
        self.make_asset()
        configs=[replace(self.config,parameters={'distance_range_m':[2.15,2.4]}),
                 replace(self.config,case_type='case2',parameters={'handle':{'body':'target','axis_local':[0,1,0],'source':'synthetic_verified_annotation','verified':True},'desired_direction_world':[0,-1,0]}),
                 replace(self.config,case_type='case3',asset_pool=str(self.root/'pool.json'),editable=['target','obstacle'],allowed_operations=['move','rotate','add'],
                         parameters={'obstacle':'obstacle','obstacle_asset':'block','approach_offset_m':[-.2,0,0],'obstacle_distance_range_m':[.15,.25]}),
                 replace(self.config,case_type='case1.5',parameters={'frame_body':'table','side_a':[0,-.7,-.4],'side_b':[0,.7,-.4],
                         'min_distance_difference_m':.25,'clearance_radius_m':.8,'min_clearance_difference_m':.1})]
        for i,config in enumerate(configs):
            path=run_build(self.source,self.robot,config,self.collection,images=False,regression=False,build_id=f'case-{i}')
            result=read_json(path/'result.json')
            self.assertEqual(result['status'],'candidate_ready_regression_pending',result)
            self.assertGreater(result['cost']['request_cumulative']['edits'],0)
            self.assertEqual(result['results']['case_condition']['status'],'unknown')
        other=self.root/'other';other.mkdir()
        source,robot,config,collection=tabletop(other,variant=1)
        path=run_build(source,robot,config,collection,images=False,regression=False,build_id='second-layout')
        self.assertEqual(read_json(path/'result.json')['status'],'candidate_ready_regression_pending')

    def test_query_views_and_geometry_decisions(self):
        self.stable(); packet=observe(self.session,candidates(self.session),self.root/'queries')
        base={'revision':packet['revision'],'observation_id':packet['observation_id']}
        for decision in ({**base,'action':'request_views','views':['diagnostic_side']},
                         {**base,'action':'query_geometry','region_id':packet['regions'][0]['region_id']},
                         {**base,'action':'abandon','reason':'uncertain_semantics'}):
            self.assertEqual(decision,parse_decision(json.dumps(decision),packet))
        gateway=DecisionGateway(None,budget=1)
        with self.assertRaises(RuntimeError):gateway.decide(packet)

    def test_edit_budget_and_group_failure_do_not_bypass_lifecycle(self):
        self.stable(); self.session.config=replace(self.config,budget=BuildBudget(edits=0))
        with self.assertRaises(RuntimeError):self.session.transact([self.move()],revision=self.session.revision)
        self.session.sim.begin(); cp=self.session.sim.state_vector().copy()
        with self.assertRaises(RuntimeError):self.session.transact([self.move()],revision=self.session.revision)
        np.testing.assert_array_equal(cp,self.session.sim.state_vector())

    def test_unqualified_region_no_guessed_tabletop(self):
        self.assertFalse(__import__('lastmile_dataflow.scenes.geometry',fromlist=['extract_regions']).extract_regions(self.session.sim,'target'))
        with self.assertRaises(ValueError):self.session.select_region('target','missing')

    def test_offline_cached_initialization_and_lazy_structural_rebuild(self):
        foundation=self.root/'foundation'
        sim=Simulation.from_source(self.source,self.robot,target='target')
        sim.freeze(foundation);sim.close()
        source_bytes=Path(self.source.xml_path).read_bytes()
        Path(self.source.xml_path).unlink()
        cached=BuildSession(self.source,self.robot,self.config,self.collection,initial_frozen_dir=foundation)
        try:
            self.assertIsNone(cached.spec)
            cached.settle(); self.assertTrue(cached.validate()['valid'])
            # Freeze and restore without either raw scene or robot XML online.
            robot_bytes=Path(self.robot.model_path).read_bytes();Path(self.robot.model_path).unlink()
            cached.freeze(self.root/'offline-freeze',build_id='offline')
            restored=Simulation.from_snapshot(self.root/'offline-freeze',self.robot,target='target');restored.close()
            Path(self.robot.model_path).write_bytes(robot_bytes)
        finally:cached.close();Path(self.source.xml_path).write_bytes(source_bytes)

    def test_snapshot_state_identity_after_checksum_rewrite(self):
        self.stable();self.session.freeze(self.root/'identity',build_id='identity')
        p=self.root/'identity/initial.npz'
        with np.load(p,allow_pickle=False) as saved:values={k:saved[k].copy() for k in saved.files}
        values['state'][0]+=.125  # time is part of full integration state
        with p.open('wb') as f:np.savez_compressed(f,**values)
        manifest=read_json(self.root/'identity/checksums.json');manifest['initial.npz']=file_digest(p)
        write_json(self.root/'identity/checksums.json',manifest)
        with self.assertRaises(ValueError):Simulation.from_snapshot(self.root/'identity',self.robot,target='target')

    def test_nonfinite_edit_keeps_serializable_failure_record(self):
        self.stable();self.session.path=self.root/'nan-build';before=self.session.checkpoint_id
        entry=self.session.transact([{'op':'move','instance':'target','pose':[float('nan'),0,.5,1,0,0,0]}],revision=self.session.revision)
        self.assertEqual(entry['status'],'rejected');self.assertEqual(before,self.session.checkpoint_id)
        self.assertTrue((self.session.path/'transactions/0000.json').exists())

    def test_supervisor_native_startup_timeout_and_success(self):
        from lastmile_dataflow.workflows.build import supervised_build
        timeout_config=replace(self.config,budget=BuildBudget(timeout_s=.03))
        path=supervised_build(self.source,self.robot,timeout_config,self.collection,images=False,regression=False,build_id='supervisor-timeout')
        result=read_json(path/'result.json');self.assertEqual(result['status'],'budget_exhausted')
        self.assertTrue(result['supervisor']['native_worker_reaped'])
        path=supervised_build(self.source,self.robot,self.config,self.collection,images=False,regression=False,build_id='supervisor-success')
        self.assertEqual(read_json(path/'result.json')['status'],'candidate_ready_regression_pending')

    def test_v1_unchanged_v2_strict_budget(self):
        with self.assertRaises(ValueError):TaskConfig(allowed_edits=['move'])
        with self.assertRaises(ValueError):replace(self.config,schema_version='1.0')
        with self.assertRaises(ValueError):replace(self.config,allowed_operations=['scale'])
        b=RequestBudget(BuildBudget(candidates=1));b.consume('candidates')
        with self.assertRaises(RuntimeError):b.consume('candidates')
        path=run_build(self.source,self.robot,replace(self.config,budget=BuildBudget(candidates=0)),self.collection,images=False,regression=False,build_id='budget')
        self.assertEqual(read_json(path/'result.json')['status'],'budget_exhausted')

if __name__=='__main__':unittest.main()
