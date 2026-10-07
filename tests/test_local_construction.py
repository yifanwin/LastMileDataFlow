"""Local context, exact routing, measured asset adapters and observed initialization."""
from dataclasses import replace
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import mujoco
import numpy as np

from case_edit_helpers import table_scene
from case_edit_helpers import template
from lastmile_dataflow.agents.construction_strategy import choose_strategy
from lastmile_dataflow.catalog.library import ThorLibrary
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.construction.case_schema import ContractError
from lastmile_dataflow.construction.scene_request import SceneInput, SceneConstructionRequest, LocalConstructionConfig
from lastmile_dataflow.io import file_digest
from lastmile_dataflow.runtime.preparation import prepare_scene, settle_scene, SettleConfig
from lastmile_dataflow.runtime.simulation import InitializationError, Simulation
from lastmile_dataflow.runtime.build_session import migrate_state
from lastmile_dataflow.runtime.visible_initialization import prepare_visible
from lastmile_dataflow.scenes.graph import SceneGraph
from lastmile_dataflow.scenes.local_context import sample_contexts, crop_context, ContextUnavailable, agent_context
from lastmile_dataflow.recording.edit_views import ViewConfig
from lastmile_dataflow.recording.head_views import render_head_pair


class LocalConstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source, self.robot = table_scene(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_alias_exact_and_split(self):
        root = self.root/'procthor-10k-val'; root.mkdir()
        (root/'val_103.xml').write_text('<mujoco/>')
        (root/'val_103_metadata.json').write_text('{}')
        for alias in ('val-103', 'val_103'):
            s = SceneInput('single', scene_id=alias, dataset_dir=str(root)).resolve()
            self.assertEqual(s.scene_id, 'procthor-10k-val/val_103')
        with self.assertRaises(ContractError):
            SceneInput('single', scene_id='train-103', dataset_dir=str(root)).resolve()
        with self.assertRaises(ContractError):
            SceneInput('single', scene_id='near kitchen', dataset_dir=str(root)).resolve()
        with self.assertRaises(NotImplementedError):
            SceneInput('dataset', dataset_dir=str(root)).resolve()
        with self.assertRaises(ContractError):
            SceneConstructionRequest('x', SceneInput('single', scene_id='x', xml_path='x'), 'case4')

    def test_source_stability_optional_but_edit_scope_still_checked(self):
        original = mujoco.mj_objectVelocity

        def unstable_neighbor(model, data, kind, body, velocity, local):
            original(model, data, kind, body, velocity, local)
            if model.body(body).name == 'neighbor':
                velocity[3] = 100.

        with patch('mujoco.mj_objectVelocity', side_effect=unstable_neighbor):
            with self.assertRaises(InitializationError):
                prepare_scene(self.source, self.robot, base=[0,0,0])
            config = SettleConfig(require_source_stability=False, support_tolerance_m=.02)
            with prepare_scene(self.source, self.robot, base=[0,0,0], settle_config=config) as p:
                self.assertTrue(p.settling['valid'])
                self.assertFalse(p.settling['stable'])
                self.assertEqual(p.graph.stage, 'observed')
                self.assertEqual(p.graph_config.support_tolerance_m, .02)
                self.assertAlmostEqual(p.settling['simulated_s'], config.settle_s)
                target_only = settle_scene(p.sim, config, stability_instances={'target'})
                self.assertTrue(target_only['stable'])
                self.assertFalse(target_only['observed_scene_stable'])
                neighbour = settle_scene(p.sim, config, stability_instances={'neighbor'})
                self.assertFalse(neighbour['valid'])
        with self.assertRaises(ValueError):
            SettleConfig(require_source_stability='false')

    def test_local_crop_preserves_nearby_geometry_and_excludes_remote_floor_children(self):
        with prepare_scene(self.source, self.robot, base=[0,0,0]) as p:
            graph = p.graph.to_dict()
            graph['nodes']['remote'] = {**graph['nodes']['target'], 'pose': {'position': [20,20,.49], 'quaternion_wxyz': [1,0,0,0]},
                'collision_bounds_world': [[19,19,.4],[21,21,.6]]}
            graph['edges'].append({'predicate': 'supported_by', 'args': ['remote','world:floor'], 'source': 'fixture'})
            full = SceneGraph.from_dict(graph)
            context = crop_context(full, 'target', 'table', 2.5)
            self.assertIn('neighbor', context.graph.nodes)
            self.assertIn('world:floor', context.graph.nodes)
            self.assertNotIn('remote', context.graph.nodes)
            self.assertIn('remote', full.nodes)
            with self.assertRaises(ContextUnavailable):
                crop_context(full, 'target', 'table', 2.5, max_nodes=1)
            first, _ = sample_contexts(full, LocalConstructionConfig(), seed=42)
            second, _ = sample_contexts(full, LocalConstructionConfig(), seed=42)
            self.assertEqual([c.context_id for c in first], [c.context_id for c in second])

    def test_agent_projection_preserves_ids_regions_and_full_physics(self):
        import json
        with prepare_scene(self.source, self.robot, base=[0,0,0]) as p:
            context = crop_context(p.graph, 'target', 'table', 2.5)
            original = copy.deepcopy(context.to_dict())
            projected = agent_context(context)
            self.assertEqual(context.to_dict(), original)
            self.assertEqual(set(projected['graph']['nodes']), set(original['graph']['nodes']))
            self.assertEqual(projected['graph']['source_graph_id'], original['graph']['graph_id'])
            self.assertEqual([r['region_id'] for r in projected['work_regions']],
                             [r['region_id'] for r in original['work_regions']])
            self.assertLess(len(json.dumps(projected)), len(json.dumps(original)))
            self.assertNotIn('support_observations', projected['graph']['nodes']['target'])
            self.assertEqual(projected['graph']['nodes']['target']['pose']['position'],
                             [round(n, 6) for n in original['graph']['nodes']['target']['pose']['position']])

    def test_context_sampling_covers_supports_before_repeating_crowded_surface(self):
        with prepare_scene(self.source, self.robot, base=[0,0,0]) as p:
            value = p.graph.to_dict()
            for i in range(12):
                name = f'crowded_{i}'
                value['nodes'][name] = copy.deepcopy(value['nodes']['target'])
                value['edges'].append({'predicate': 'supported_by', 'args': [name, 'table']})
            value['nodes']['other_table'] = copy.deepcopy(value['nodes']['table'])
            value['nodes']['other_target'] = copy.deepcopy(value['nodes']['target'])
            value['edges'].append({'predicate': 'supported_by', 'args': ['other_target', 'other_table']})
            graph = SceneGraph.from_dict(value)
            original = graph.to_dict()
            config = replace(LocalConstructionConfig(), max_contexts=2)
            for seed in (0, 42, 99):
                contexts, _ = sample_contexts(graph, config, seed=seed)
                self.assertEqual({c.support for c in contexts}, {'table', 'other_table'})
            self.assertEqual(graph.to_dict(), original)

    def test_asset_category_retrieval_measured_z_up_without_source_changes(self):
        root = self.root/'thor'; folder = root/'Kitchen Objects/Cup/Prefabs/Cup_fixture'; folder.mkdir(parents=True)
        source = folder/'Cup_fixture.xml'
        source.write_text('<mujoco><worldbody><body name="cup"><freejoint/><geom type="box" size=".1 .2 .15" mass="1"/></body></worldbody></mujoco>')
        before = file_digest(source)
        library = ThorLibrary(root)
        self.assertEqual(library.categories(), ['Cup'])
        assets = library.retrieve([{'categories': ['Cup'], 'intended_role': 'obstacle', 'size_constraints_m': {}}], self.root/'retrieval')
        description = assets.describe()
        self.assertEqual(len(description), 1, description)
        self.assertAlmostEqual(description[0]['size']['height'], .4)
        self.assertAlmostEqual(description[0]['size']['depth'], .3)
        self.assertEqual(description[0]['root_motion'], 'free')
        self.assertEqual(file_digest(source), before)

    def test_case15_contrast_requires_three_distinct_unambiguous_roles(self):
        with prepare_scene(self.source, self.robot, base=[0,0,0]) as p:
            context = crop_context(p.graph, 'target', 'table', 2.5)
        regions = [r['region_id'] for r in context.work_regions()]
        roles = ('base_space_constrained', 'arm_unfavorable', 'preferred')
        valid = {'decision': 'select', 'context_id': context.context_id, 'radius_m': 2.5,
                 'edit_directions': ['move'], 'expected_layout': 'hypothesis only',
                 'contrast_spec': [{'work_region_id': region, 'role': role,
                                    'expected_mechanism': 'not verified'}
                                   for region, role in zip(regions, roles)],
                 'asset_requests': [], 'rationale': 'fixture', 'unverified_claims': []}

        class Gateway:
            def __init__(self, value):
                self.value = value

            def call(self, role, prompt, payload, parser):
                return parser(self.value)

        case = replace(template(), case_type='case1.5')
        self.assertEqual(choose_strategy(case, [context], Gateway(valid), LocalConstructionConfig()).decision, 'select')
        for contrasts in (
            valid['contrast_spec'][:2],
            [valid['contrast_spec'][0], {**valid['contrast_spec'][1], 'work_region_id': regions[0]}, valid['contrast_spec'][2]],
            [valid['contrast_spec'][0], {**valid['contrast_spec'][1], 'work_region_id': regions[0]},
             valid['contrast_spec'][2], {**valid['contrast_spec'][2], 'work_region_id': regions[3]}],
        ):
            with self.assertRaises(ContractError):
                choose_strategy(case, [context], Gateway({**valid, 'contrast_spec': contrasts}), LocalConstructionConfig())

    @unittest.skipUnless(os.environ.get('CASE_EDIT_RENDER_TESTS') == '1', 'opt-in actual RGB, synthetic model')
    def test_visible_initialization_independent_and_same_head_enforced(self):
        cfg = LocalConstructionConfig(min_target_pixels=8)
        with prepare_scene(self.source, self.robot, base=[0,0,0]) as p:
            context = crop_context(p.graph, 'target', 'table', 2.5)
            original = p.sim.state_vector().copy()
            prepared, obs = prepare_visible(p, context, cfg, CollectionConfig(), self.root/'init', seed=42,
                deadline=time.monotonic()+30, view_config=ViewConfig(width=320, height=240))
            try:
                np.testing.assert_array_equal(p.sim.state_vector(), original)
                self.assertGreaterEqual(obs['visibility']['visible_pixels'], 8)
                valid = [row for row in prepared.initialization['trials'] if row['valid']]
                self.assertEqual(len(valid), 3)
                self.assertEqual(obs['visibility']['visible_pixels'], max(row['final_visibility']['visible_pixels'] for row in valid))
                self.assertTrue(all(Path(row['head_rgb']).is_file() for row in valid))
                packet = render_head_pair(prepared.sim, prepared.sim, prepared.graph, prepared.graph, obs,
                    self.root/'pair', sample_id='x', target='target', min_target_pixels=8, config=ViewConfig(width=320,height=240))
                self.assertTrue(packet['valid'])
                initial = prepared.sim.robot.config.initial.copy()
                after = Simulation(prepared.sim.model, prepared.sim.robot.config, copy.deepcopy(prepared.sim.catalog))
                try:
                    migrate_state(prepared.sim, after)
                    slight_drift = copy.deepcopy(prepared.initialization['selected_initial'])
                    slight_drift['head'][1] += .005
                    after.robot.initialize(slight_drift)
                    strict = render_head_pair(prepared.sim, after, prepared.graph, prepared.graph, obs,
                        self.root/'strict_pair', sample_id='strict', target='target', min_target_pixels=8,
                        config=ViewConfig(width=320, height=240))
                    self.assertFalse(strict['valid'])
                    relaxed = render_head_pair(prepared.sim, after, prepared.graph, prepared.graph, obs,
                        self.root/'relaxed_pair', sample_id='relaxed', target='target', min_target_pixels=8,
                        config=ViewConfig(width=320, height=240, initial_group_tolerance=.02,
                                          camera_position_tolerance_m=.02, camera_matrix_tolerance=.03))
                    self.assertTrue(relaxed['valid'])
                    self.assertEqual(relaxed['initial_state_check']['group_tolerance'], .02)
                finally:
                    after.close()
                initial['base'] = [0,0,0]
                prepared.sim.robot.initialize(initial)
                with self.assertRaisesRegex(ValueError, 'baseline changed'):
                    render_head_pair(prepared.sim, prepared.sim, prepared.graph, prepared.graph, obs,
                        self.root/'stale', sample_id='x', target='target')
            finally:
                prepared.close()
