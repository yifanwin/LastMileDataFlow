"""Shared preparation and real MuJoCo edit transactions, without rendering/GPU."""
import copy
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np

from lastmile_dataflow.scenes.build.context import BuildContext
from lastmile_dataflow.scenes.build.proposal import AddObject, BasePoseEdit, BuildProposal, MoveTargetOnSupport, ProbeResult
from lastmile_dataflow.scenes.build.builder import search_variant
from lastmile_dataflow.scenes.build.repair import repair_allowed
from lastmile_dataflow.scenes.build.validation import proposal_scene
from lastmile_dataflow.scenes.dataset_config import TaskModeRecipe
from lastmile_dataflow.scenes.loading.asset_profiles import AssetProfile
from lastmile_dataflow.scenes.modes.geometry import asset_candidates, resolve_assets, nominal
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.stations.sampling import select_task_stations
from lastmile_dataflow.utils.io import write_rows
from tests.helpers import table_scene


class UnknownVisibility:
    """No pixels are claimed by these physics/search tests."""
    def __init__(self, *args):
        pass

    def measure(self, sim):
        return {'status': 'unknown', 'complete': False, 'value': None, 'known_max': None}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FixtureBuilder:
    def __init__(self, proposal, repair=None):
        self.proposal, self.repaired = proposal, repair

    def propose(self, ctx, rng):
        proposal, self.proposal = self.proposal, None
        return proposal

    def cheap_validate(self, ctx, proposal, **kwargs):
        return ProbeResult('pass', 'synthetic_proxy_for_search_protocol')

    def repair(self, ctx, proposal, checks):
        repaired, self.repaired = self.repaired, None
        return repaired


class BuildContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        source, robot = table_scene(self.root)
        tree = ET.parse(source.xml_path)
        asset = ET.SubElement(tree.getroot(), 'asset')
        ET.SubElement(asset, 'mesh', name='bounds', vertex='-4 -3 0  5 -3 0  5 3 0  -4 3 0 '
                      '-4 -3 -.02  5 -3 -.02  5 3 -.02  -4 3 -.02')
        ET.SubElement(tree.getroot().find('worldbody'), 'geom', name='room_floor', type='mesh',
                      mesh='bounds', contype='0', conaffinity='0', rgba='0 0 0 0')
        tree.write(source.xml_path)
        self.source = Simulation.from_source(source, robot, target='target')
        grasps = self.root / 'assets/grasps/droid/bowl'
        grasps.mkdir(parents=True)
        np.savez(grasps / 'bowl_grasps_filtered.npz', transforms=np.eye(4)[None, :, :])
        self.task = {'task_id': 'pick-target', 'target_body': 'target', 'instance_id': 'target',
                     'operation': 'pick', 'category': 'Bowl', 'asset_id': 'bowl',
                     'anchor_world': [2., 0., .5], 'local_bounds': [[-.04, -.04, -.05], [.04, .04, .05]],
                     'instruction': 'Pick the bowl.'}
        xml = self.root / 'blocker.xml'
        xml.write_text('<mujoco><worldbody><body name="blocker"><freejoint/>'
                       '<geom name="blocker_geom" type="box" size=".1 .1 .1" mass="1"/>'
                       '</body></worldbody></mujoco>')
        profile = AssetProfile('blocker', str(xml), 'Chair', width_m=.2, depth_m=.2, height_m=.2,
            footprint_m2=.04, movable=True, loadable=True, support_kind='floor',
            compatible_supports=('floor',), roles=('floor_obstacle',), xml_path=str(xml), root_body='blocker')
        write_rows(self.root / 'assets.jsonl', [asdict(profile)])
        self.recipe = TaskModeRecipe(dataset_dir=str(self.root), assets_dir=str(self.root / 'assets'),
            asset_index=str(self.root / 'assets.jsonl'), houses=[0],
            robot_config='unused', mode_config='configs/task_modes/mode_defaults.json',
            station_config={'map_resolution_m': .1})
        self.ctx = BuildContext.prepare(self.source, self.task, self.recipe)
        self.placement = {'asset_id': 'blocker', 'category': 'Chair', 'kind': 'ground',
            'pose': {'position': [-1., 0., .103], 'quaternion_wxyz': [1., 0., 0., 0.]},
            'support_geom': None, 'support_height_m': 0.}

    def tearDown(self):
        self.ctx.close()
        self.source.close()
        self.tmp.cleanup()

    def test_no_edit_and_base_pose_reuse_baseline_without_settling_or_mutating_source(self):
        before = self.source.state_vector().copy()
        baseline = self.ctx.baseline.state_vector().copy()
        with patch.object(self.ctx.session, 'candidate', side_effect=AssertionError('no-edit rebuilt physics')):
            for proposal in (BuildProposal('natural', 'reachability'),
                             BuildProposal('yaw', 'orientation', pose_edit=BasePoseEdit([{'xy': [0., 0.], 'yaw': .7}]))):
                with proposal_scene(self.ctx, proposal) as (sim, geometry, checks):
                    self.assertIs(sim, self.ctx.baseline)
                    self.assertIs(geometry, self.ctx.geometry)
                    self.assertTrue(checks['valid'])
                    self.assertEqual(checks['task_completion'], 'unknown')
        np.testing.assert_array_equal(self.source.state_vector(), before)
        np.testing.assert_array_equal(self.ctx.baseline.state_vector(), baseline)
        self.assertEqual(len(self.ctx.geometry.lattice), 49)
        self.assertTrue(self.ctx.task['require_data_grasps'])

    def test_added_object_settles_with_matched_control_and_fresh_geometry(self):
        before = self.ctx.baseline.state_vector().copy()
        proposal = BuildProposal('ground', 'ground_obstacle', edits=[AddObject(self.placement)])
        with proposal_scene(self.ctx, proposal) as (sim, geometry, checks):
            self.assertTrue(checks['valid'], checks)
            self.assertIsNot(geometry.occupancy, self.ctx.geometry.occupancy)
            self.assertTrue(checks['support']['objects'][0]['load_bearing_contact'])
            self.assertEqual(checks['source_effects']['same_time_s'], sim.data.time)
            candidate = sim
        self.assertTrue(candidate.closed)
        np.testing.assert_array_equal(self.ctx.baseline.state_vector(), before)

    def test_move_target_uses_same_model_new_state_and_checks_support_without_failing_intended_edit(self):
        before = self.ctx.baseline.state_vector().copy()
        support = self.ctx.geometry.supports[0]
        move = MoveTargetOnSupport('target', {'position': [1.8, 0., .493],
                                            'quaternion_wxyz': [1., 0., 0., 0.]}, support)
        proposal = BuildProposal('reach', 'reachability', edits=[move])
        with proposal_scene(self.ctx, proposal) as (sim, geometry, checks):
            self.assertTrue(checks['valid'], checks)
            self.assertIs(sim.model, self.ctx.baseline.model)
            self.assertIsNot(sim.data, self.ctx.baseline.data)
            self.assertAlmostEqual(geometry.task['anchor_world'][0], 1.8, places=3)
            self.assertEqual(geometry.supports[0].geom, support.geom)
            self.assertIn('target', checks['edited_source_bodies'])
            self.assertNotIn('target', [row['instance'] for row in checks['source_effects']['source_objects']])
            self.assertTrue(checks['collision']['valid'])
        np.testing.assert_array_equal(self.ctx.baseline.state_vector(), before)

    def test_collision_rejects_candidate_before_settling(self):
        placement = copy.deepcopy(self.placement)
        placement['pose']['position'] = [2., 0., .49]
        proposal = BuildProposal('collision', 'ground_obstacle', edits=[AddObject(placement)])
        with patch.object(self.ctx.session, 'settle', side_effect=AssertionError('colliding proposal was stepped')):
            with proposal_scene(self.ctx, proposal) as (sim, geometry, checks):
                self.assertFalse(checks['valid'])
                self.assertIsNone(geometry)
                self.assertEqual(sim.data.time, self.ctx.baseline.data.time)

    def test_repair_is_local_and_does_not_mutate_original_request(self):
        original = BuildProposal('ground', 'ground_obstacle', edits=[AddObject(self.placement)])
        repaired = copy.deepcopy(original)
        repaired.edits[0].placement['pose']['position'][0] += .03
        self.assertTrue(repair_allowed(self.ctx, original, repaired))
        repaired.edits[0].placement['pose']['position'][0] += .1
        self.assertFalse(repair_allowed(self.ctx, original, repaired))
        changes = original.runtime_changes()
        changes['placements'][0]['pose']['position'][0] = 9.
        self.assertEqual(original.edits[0].placement['pose']['position'][0], -1.)

    def test_data_grasp_gate_does_not_use_bbox_fallback_for_formal_pick(self):
        missing = {**self.task, 'asset_id': 'no_data'}
        with self.assertRaisesRegex(ValueError, 'no_valid_data_grasps'):
            BuildContext.prepare(self.source, missing, self.recipe)

    def test_profile_proposal_defers_compile_and_resolves_off_center_root(self):
        profile = self.ctx.selector.profiles[0]
        xml = self.root / 'blocker.xml'
        tree = ET.parse(xml)
        tree.getroot().find('.//geom').set('pos', '2 0 0')
        tree.write(xml)
        edit = next(asset_candidates(self.ctx, [profile], [[-1., 0.]], floor=True))
        self.assertEqual(self.ctx.assets.compile_count, 0)
        proposal = BuildProposal('lazy', 'ground_obstacle', edits=[edit])
        resolved = resolve_assets(self.ctx, proposal)
        self.assertEqual(self.ctx.assets.compile_count, 1)
        placement = resolved.edits[0].placement
        self.assertAlmostEqual(placement['pose']['position'][0], -3.)
        bounds = placement['world_bounds']
        self.assertAlmostEqual((bounds['min'][0] + bounds['max'][0]) / 2, -1.)
        resolve_assets(self.ctx, proposal)
        self.assertEqual(self.ctx.assets.compile_count, 1)

    def test_final_station_sampling_reuses_nominal_initialization_but_measures_pixels(self):
        candidate = self.ctx.geometry.lattice[0]
        with patch('lastmile_dataflow.scenes.modes.geometry.ray_visibility',
                   return_value={'complete': False, 'value': None, 'known_max': None}):
            nominal(self.ctx, candidate)
        self.assertEqual(self.ctx.proxy_initializations, 1)
        proposal = BuildProposal('reuse', 'reachability', [candidate['station_id']])
        self.ctx.recipe = replace(self.ctx.recipe, max_final_stations=1)
        with patch('lastmile_dataflow.scenes.stations.sampling.TaskVisibility', UnknownVisibility):
            selected, sampling = select_task_stations(self.ctx, self.ctx.baseline, self.ctx.geometry, proposal)
        self.assertEqual(selected[0]['station_id'], candidate['station_id'])
        self.assertEqual(sampling['new_initializations'], 0)
        self.assertEqual(selected[0]['visibility']['status'], 'unknown')
        self.assertNotIn('visibility_proxy', selected[0])

    def test_no_edit_search_reuses_snapshot_and_initializations(self):
        root = self.root / 'library'
        with (patch('lastmile_dataflow.scenes.stations.sampling.TaskVisibility', UnknownVisibility),
              patch.object(self.ctx.baseline, 'freeze', wraps=self.ctx.baseline.freeze) as freeze):
            results = [search_variant(self.ctx, FixtureBuilder(BuildProposal(str(i), 'reachability')),
                        'reachability', root, str(i), rng=np.random.default_rng(42), task_prefix='task')
                       for i in range(2)]
        self.assertEqual(freeze.call_count, 1)
        self.assertTrue(all(row['status'] == 'accepted' for row in results))
        self.assertEqual(results[0]['scene'], results[1]['scene'])
        self.assertEqual(len(results[0]['stations']), 20)
        self.assertEqual(results[1]['build_metrics']['station_initializations'], 0)
        self.assertEqual(results[0]['task_completion'], 'unknown')
        frozen = Simulation.from_snapshot(root / results[0]['scene'], self.source.robot.config, target='target')
        try:
            np.testing.assert_allclose(frozen.state_vector(), self.ctx.baseline.state_vector())
        finally:
            frozen.close()

    def test_search_repairs_once_in_fresh_candidate_and_freezes_real_state(self):
        original = copy.deepcopy(self.placement)
        original['pose']['position'][2] = .08
        proposal = BuildProposal('ground', 'ground_obstacle', edits=[AddObject(original)])
        repaired = BuildProposal('repair', 'ground_obstacle', edits=[AddObject(self.placement)])
        with patch('lastmile_dataflow.scenes.stations.sampling.TaskVisibility', UnknownVisibility):
            result = search_variant(self.ctx, FixtureBuilder(proposal, repaired), 'ground_obstacle',
                self.root / 'edited', 'ground', rng=np.random.default_rng(42), task_prefix='task')
        self.assertEqual(result['status'], 'accepted', result)
        self.assertEqual(result['build_metrics']['physical_candidates'], 1)
        self.assertEqual(result['build_metrics']['physical_trials'], 2)
        self.assertEqual(result['build_metrics']['repairs'], 1)
        self.assertTrue(result['construction_checks']['support']['valid'])
        frozen = Simulation.from_snapshot(self.root / 'edited' / result['scene'], self.source.robot.config, target='target')
        try:
            self.assertGreater(frozen.model.nbody, self.ctx.baseline.model.nbody)
            self.assertAlmostEqual(frozen.data.xpos[frozen.model.body('procedural_obstacle_0').id, 2], .1, places=3)
        finally:
            frozen.close()

    def test_yaw_group_has_priority_and_filtered_pose_does_not_consume_budget(self):
        self.ctx.recipe = replace(self.ctx.recipe, max_final_stations=3)
        nominal = self.ctx.geometry.lattice[0]
        poses = [{**nominal, 'station_id': 'invalid', 'xy': [100., 0.], 'yaw': 0.}]
        poses.extend({**nominal, 'station_id': nominal['station_id'] + f'-Y{i}',
                      'yaw': yaw, 'yaw_source': 'explicit'}
                     for i, yaw in enumerate([0., np.pi / 4, -np.pi / 4]))
        proposal = BuildProposal('yaw', 'orientation', pose_edit=BasePoseEdit(poses))
        with patch('lastmile_dataflow.scenes.stations.sampling.TaskVisibility', UnknownVisibility):
            selected, sampling = select_task_stations(self.ctx, self.ctx.baseline, self.ctx.geometry, proposal)
        self.assertEqual([row['station_id'] for row in selected], [row['station_id'] for row in poses[1:]])
        self.assertEqual(len({row['xy_id'] for row in selected}), 1)
        np.testing.assert_allclose([row['base'][2] for row in selected], [0., np.pi / 4, -np.pi / 4])
        self.assertEqual(sampling['new_initializations'], 4)
        self.assertEqual(len(sampling['rejected_stations']), 1)
        self.assertEqual(sampling['pose_budget_fraction'], 1.)

    def test_unknown_final_proxy_is_rejected_with_its_evidence_and_not_frozen(self):
        self.ctx.settings = replace(self.ctx.settings, max_proposals=1)
        builder = FixtureBuilder(BuildProposal('reach', 'reachability'))
        def validate(ctx, proposal, **kwargs):
            return ProbeResult('unknown', 'incomplete_visibility') if kwargs.get('stations') is not None else ProbeResult('pass', 'geometry_pass')
        builder.cheap_validate = validate
        with (patch('lastmile_dataflow.scenes.stations.sampling.TaskVisibility', UnknownVisibility),
              patch.object(self.ctx.baseline, 'freeze', side_effect=AssertionError('unknown proxy was frozen'))):
            result = search_variant(self.ctx, builder, 'reachability', self.root / 'unknown', 'reach',
                rng=np.random.default_rng(42), task_prefix='task')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['reason'], 'incomplete_visibility')
        self.assertEqual(result['last_checks']['final_proxy']['status'], 'unknown')
        self.assertEqual(len(result['last_checks']['station_evidence']), 20)
