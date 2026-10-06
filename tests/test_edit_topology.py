import json
import tempfile
import unittest
from pathlib import Path
from case_edit_helpers import table_scene, template, proposal
from lastmile_dataflow.catalog.edit_assets import EditAssetCatalog
from lastmile_dataflow.construction.compiler import compile_sample, SearchExhausted
from lastmile_dataflow.construction.dsl import SymbolicDSL
from lastmile_dataflow.runtime.preparation import prepare_scene
from lastmile_dataflow.runtime.case_edit_session import CaseEditSession


def asset_fixture(root):
    root = Path(root)
    (root/'asset.xml').write_text('''<mujoco><worldbody><body name="box"><freejoint name="free"/>
    <geom name="geom" type="box" size=".03 .03 .04" mass=".1"/></body></worldbody></mujoco>''')
    (root/'assets.json').write_text(json.dumps({'asset_catalog_version': '0.1', 'assets': [
        {'asset_id': 'box1', 'xml_path': 'asset.xml', 'root_body': 'box', 'category': 'box', 'type': 'object'}]}))
    return EditAssetCatalog(root/'assets.json')


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source, robot = table_scene(self.root)
        self.prepared = prepare_scene(source, robot, base=[0, 0, 0])
        self.assets = asset_fixture(self.root)
        self.session = CaseEditSession(self.prepared, assets=self.assets)

    def tearDown(self):
        self.session.close()
        self.prepared.close()
        self.tmp.cleanup()

    def test_unqualified_asset_can_load_size_filter(self):
        spec, asset = self.assets.load('box1')
        self.assertAlmostEqual(asset['size']['height'], .08)
        import numpy as np
        self.assertEqual(self.assets.select({'category': ['box'], 'size': {'height': [.07, .09]}}, np.random.default_rng(1))['asset_id'], 'box1')
        with self.assertRaises(ValueError):
            self.assets.select({'size': {'height': [1, 2]}}, np.random.default_rng(1))

    def test_add_then_rotate_remove_neighbor_and_rollback(self):
        p = proposal()
        p = SymbolicDSL('topology', {**p.bindings, 'neighbor': 'neighbor'}, [
            {'op': 'add', 'bind_as': '$new', 'asset_selector': {'category': ['box']},
             'search_space': {'support': '$support', 'xy': {'mode': 'uniform', 'x': [-.25, -.25], 'y': [.2, .2]}}},
            {'op': 'rotate', 'subject': '$new', 'axis': [0, 0, 1], 'angle': {'value': 1.}},
            {'op': 'remove', 'subject': '$neighbor'}],
            goals=[{'predicate': 'supported_by', 'args': ['$new', '$support'], 'value': True}])
        exe = compile_sample(p, template(), self.prepared.graph, assets=self.assets)
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'pending_review', trial.to_dict())
        added = exe.sampled_parameters['bindings']['new']
        self.assertIn(added, trial.after_graph.nodes)
        self.assertNotIn('neighbor', trial.after_graph.nodes)
        self.assertEqual(trial.after_graph.nodes[added]['asset_id'], 'box1')
        self.session.reject()
        self.assertNotIn(added, self.prepared.refresh().nodes)
        self.assertIn('neighbor', self.prepared.graph.nodes)

    def test_missing_deleted_reference_and_required_role_conflict(self):
        p = proposal()
        p = SymbolicDSL('delete', p.bindings, [{'op': 'remove', 'subject': '$target'}])
        with self.assertRaises(SearchExhausted):
            compile_sample(p, template(), self.prepared.graph)
        p = SymbolicDSL('delete', {**proposal().bindings, 'other': 'neighbor'}, [
            {'op': 'remove', 'subject': '$other'},
            {'op': 'rotate', 'subject': '$other', 'axis': [0, 0, 1], 'angle': {'value': 1}}])
        with self.assertRaises(SearchExhausted):
            compile_sample(p, template(), self.prepared.graph)

    def test_carry_support_expands_actual_contacts_and_final_support(self):
        p = SymbolicDSL('carry', proposal().bindings, [{'op': 'move', 'subject': '$support',
              'carry_supported': True, 'search_space': {'frame': 'world',
              'xy': {'mode': 'uniform', 'x': [4, 4], 'y': [1, 1]}}}])
        exe = compile_sample(p, template(), self.prepared.graph)
        self.assertEqual({o['instance'] for o in exe.operations}, {'table', 'target', 'neighbor'})
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'pending_review', trial.to_dict())
        self.assertEqual(trial.after_graph.nodes['target']['support_status'], 'pass')

    def test_failed_topology_restores_model_and_state(self):
        p = SymbolicDSL('badadd', proposal().bindings, [{'op': 'add', 'bind_as': '$new',
             'asset_selector': {'asset_id': 'box1'}, 'search_space': {'frame': 'world',
             'xy': {'mode': 'uniform', 'x': [10, 10], 'y': [0, 0]}}}],
             goals=[{'predicate': 'supported_by', 'args': ['$new', '$support'], 'value': True}])
        exe = compile_sample(p, template(), self.prepared.graph, assets=self.assets)
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'rolled_back')
        self.assertIs(self.session.sim, self.prepared.sim)
        self.assertFalse(self.session.extra_instances)

    def test_add_static_support_then_place_target_uses_new_region(self):
        (self.root/'stand.xml').write_text('''<mujoco><worldbody><body name="stand">
        <geom name="top" type="box" size=".4 .4 .1"/></body></worldbody></mujoco>''')
        (self.root/'stands.json').write_text(json.dumps({'asset_catalog_version': '0.1', 'assets': [
            {'asset_id': 'stand', 'xml_path': 'stand.xml', 'root_body': 'stand', 'type': 'surface'}]}))
        assets = EditAssetCatalog(self.root/'stands.json')
        self.session.assets = assets
        p = SymbolicDSL('new-support', {'target': 'target', 'floor': 'world:floor'}, [
            {'op': 'add', 'bind_as': '$support', 'asset_selector': {'asset_id': 'stand'},
             'search_space': {'support': '$floor', 'xy': {'mode': 'uniform', 'x': [4, 4], 'y': [0, 0]}}},
            {'op': 'move', 'subject': '$target', 'search_space': {'support': '$support'}}])
        exe = compile_sample(p, template(), self.prepared.graph, assets=assets)
        self.assertGreater(exe.operations[-1]['pose']['position'][0], 3.6)
        trial = self.session.execute(template(), p, exe)
        self.assertEqual(trial.status, 'pending_review', trial.to_dict())

    def test_carry_does_not_use_stale_relation_after_explicit_child_move(self):
        p = SymbolicDSL('carry-after-child', proposal().bindings, [
             {'op': 'move', 'subject': '$target', 'search_space': {'support': 'world:floor',
              'xy': {'mode': 'uniform', 'x': [5, 5], 'y': [0, 0]}}},
             {'op': 'move', 'subject': '$support', 'carry_supported': True,
              'search_space': {'frame': 'world', 'xy': {'mode': 'uniform', 'x': [4, 4], 'y': [1, 1]}}}])
        exe = compile_sample(p, template(), self.prepared.graph)
        self.assertEqual(sum(o['instance'] == 'target' for o in exe.operations), 1)
