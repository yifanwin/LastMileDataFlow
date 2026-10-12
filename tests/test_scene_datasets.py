"""Contract/selection/fault regression. Fixtures do not prove real scene construction."""
from dataclasses import asdict, replace
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.construction.scene_dataset_config import SceneDatasetRecipe, SceneStationConfig, house_number
from lastmile_dataflow.construction.obstacle_config import ObstacleConfig
from lastmile_dataflow.io import digest, file_digest, read_json, write_json
from lastmile_dataflow.stations.scene_package import package_manifest, robot_descriptor, select_stations, bind_package, package_stations
from lastmile_dataflow.stations.no_edit_config import NoEditConfig, load_no_edit_config
from lastmile_dataflow.workflows.scene_datasets import spatial_select, _binding, qualify_scene_discovery, audit_scene_dataset


class PortableAuditTests(unittest.TestCase):
    def test_companion_bound_to_dataset_manifest(self):
        # Empty contract fixture, not a physical scene acceptance test.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'dataset'; companion = Path(directory) / 'audit_inputs'
            write_json(root / 'manifest.json', {'scene_dataset_id': 'one', 'sample_count': 0})
            write_json(root / 'checksums.json', {})
            (root / 'samples.jsonl').write_text('')
            bound = {'schema_version': 'scene-audit-inputs-v1', 'scene_dataset_id': 'one',
                     'dataset_manifest_sha256': file_digest(root / 'manifest.json'), 'configs': {}}
            write_json(companion / 'manifest.json', bound)
            self.assertTrue(audit_scene_dataset(root, audit_inputs=companion)['valid'])
            for field, value in [('scene_dataset_id', 'other'), ('dataset_manifest_sha256', 'tampered'),
                                 ('schema_version', 'unknown')]:
                write_json(companion / 'manifest.json', {**bound, field: value})
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    audit_scene_dataset(root, audit_inputs=companion)


class RecipeTests(unittest.TestCase):
    def recipe(self, **kwargs):
        return SceneDatasetRecipe('dataset', 'library', 'robot', ['val103', 'val_104'], **kwargs)

    def test_bounded_houses_and_defaults(self):
        r = self.recipe()
        self.assertEqual(r.houses, [103, 104])
        self.assertEqual(r.max_pick_targets_per_house, 3)
        self.assertEqual(r.variants_per_case, 5)
        self.assertFalse(hasattr(r.geometry, 'operation_backend'))
        self.assertFalse(hasattr(r.geometry, 'planner_backend'))
        for value in (True, -1, 'train103', '../val103'):
            with self.assertRaises(ValueError): house_number(value)
        with self.assertRaises(ValueError): SceneDatasetRecipe('d', 'l', 'r', [])

    def test_invalid_recipe_and_sensor_settings(self):
        for kwargs in ({'case1_added_surface_count': [4, 8]}, {'case2_added_surface_count': [10, 21]},
                       {'station_selection': 'prefix'}, {'targets': {'val105': ['Cup_30']}}, {'min_final_points': 21}):
            with self.assertRaises(ValueError): self.recipe(**kwargs)
        for kwargs in ({'spacing_m': 0}, {'radius_m': float('nan')}, {'operations': ['close']}, {'min_target_pixels': 0}):
            with self.assertRaises(ValueError): SceneStationConfig(**kwargs)

    def test_new_count_rules_and_old_compatibility(self):
        old = ObstacleConfig('base', 'dataset', 'lib', 'cup', obstacle_count=[1, 3], construction={'case': 'Case1'})
        self.assertEqual(old.case_policy.rule_version, 'obstacle-cases-v1')
        new = replace(old, schema_version='procedural-obstacles-v2', obstacle_count=[6, 9],
                      construction={'case': 'Case1', 'rule_version': 'scene-cases-v2'})
        self.assertEqual(new.obstacle_count, [6, 9])
        with self.assertRaises(ValueError): replace(new, obstacle_count=[5, 9])
        with self.assertRaises(ValueError): replace(new, schema_version='procedural-obstacles-v1')
        with self.assertRaises(ValueError): replace(new, surface_categories=['Chair'])
        case2 = replace(new, obstacle_count=[10, 20], construction={'case': 'Case2', 'rule_version': 'scene-cases-v2'},
                        mandatory_categories=[], ground_categories=[], surface_categories=['Cup', 'Vase'])
        with self.assertRaises(ValueError): replace(case2, obstacle_count=[6, 12])

    def test_spatial_diversity_not_prefix(self):
        def row(i, room, support, x, area):
            return {'task': {'task_id': str(i), 'room_id': room, 'anchor_world': [x, 0, 1], 'instance_id': str(i), 'asset_id': str(i)},
                    'support_id': support, 'support_area_m2': area, 'prefilter_count': 5}
        rows = [row(0, 1, 'a', 0, 5), row(1, 1, 'a', .1, 4), row(2, 1, 'b', 3, 3), row(3, 2, 'c', 5, 2)]
        selected = spatial_select(rows, 3)
        self.assertEqual({r['support_id'] for r in selected}, {'a', 'b', 'c'})
        self.assertNotIn('1', [r['task']['task_id'] for r in selected])

    def test_visible_qualification_replaces_failed_spatial_choice(self):
        # MOCK station verdicts only; no real scene/visibility claim.
        recipe = SceneDatasetRecipe('d', 'l', 'r', [103], cases=['Case2'], max_pick_targets_per_house=3)
        tasks = [{'task_id': str(i), 'instance_id': str(i), 'asset_id': str(i), 'target_body': str(i),
            'operation': 'pick', 'room_id': 2 if i == 2 else 1, 'anchor_world': [x, 0, 1]}
            for i, x in enumerate([0, 3, 5, 7])]
        seed = {'house': 103, 'task': tasks[0], 'input_snapshot': 'snapshot', 'source': {},
                'prefilter_count': 5, 'support_id': '0', 'support_area_m2': 5}
        with tempfile.TemporaryDirectory() as directory:
            inp, out = Path(directory)/'static.json', Path(directory)/'qualified.json'
            write_json(inp, {'recipe': asdict(recipe), 'recipe_sha256': digest(asdict(recipe)), 'selected': [seed],
                'exclusions': [{'house': 103, 'task_id': str(i), 'reason': 'eligible_not_selected_by_spatial_coverage'} for i in range(1,4)],
                'houses': [{'house': 103}], 'elapsed_s': 1})
            sim = MagicMock(); sim.model.body.side_effect = lambda name: SimpleNamespace(id=int(name))
            def stations(*args, **kwargs):
                return [{'geometry': 'valid'}]* (0 if sim.target_id == 2 else 5)
            module = 'lastmile_dataflow.workflows.scene_datasets.'
            with patch(module+'load_scene_robot'), patch(module+'SceneSource'), patch(module+'read_json', wraps=read_json), \
                 patch(module+'Simulation.from_snapshot', return_value=sim), patch(module+'discover_tasks', return_value=(tasks, [])), \
                 patch(module+'task_support_obstacles', return_value=[]), \
                 patch(module+'support_regions', side_effect=lambda s,t: [SimpleNamespace(support=t['task_id'],bounds=[0,5-int(t['task_id']),0,1])]), \
                 patch(module+'evaluate_stations', side_effect=stations):
                # Metadata is irrelevant to the mocked discover_tasks.
                with patch(module+'SceneSource.procthor', return_value=SimpleNamespace(metadata_path=inp)):
                    qualify_scene_discovery(inp,out)
            result=read_json(out)
            self.assertEqual({r['task']['task_id'] for r in result['selected']}, {'0','1','3'})
            self.assertTrue(any(r.get('task_id')=='2' and r['reason']=='fewer_than_minimum_baseline_visible_points' for r in result['exclusions']))


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        model = self.root / 'robot.xml'; model.write_text('fixture robot identity only')
        self.robot = RobotConfig(str(model)); self.geometry = SceneStationConfig()
        self.package = self.root / 'package'; self.package.mkdir()
        self.task = {'task_id': 'pick-cup', 'target_body': 'cup', 'anchor_world': [0, 0, 1]}
        self.rows = [{'station_id': f'S{i}', 'xy': [.1*i, .2], 'grid_index': [i, 0],
            'geometry': 'valid', 'independent_simulation': True, 'non_robot_qpos_unchanged': True,
            'new_solver_warnings': [], 'visibility': {'target': 'cup', 'visible_pixels': 100},
            'initial': {'base': [.1*i, .2, 0]}} for i in range(6)]
        binding = _binding(self.robot, self.geometry)
        self.manifest = {'schema_version': 'scene-station-package-v1', 'status': 'sealed',
            'scene_dataset_id': 'fixture', 'sample_id': 'sample', 'task_id': 'pick-cup', 'scene_id': 'val_103',
            'robot_sensor_config_id': 'rs-' + digest(binding), 'binding': binding,
            'selection_mode': 'coverage', 'max_final_stations': 20, 'min_final_points': 5, 'station_count': 6}
        for name in ('model.mjb', 'initial.npz', 'instances.json'):
            path = self.package / 'scene' / name; path.parent.mkdir(exist_ok=True); path.write_bytes(b'fixture-not-real-snapshot')
        write_json(self.package / 'scene/version.json', {'source': {'scene_id': 'val_103'}})
        write_json(self.package / 'scene/checksums.json', {str(p.name): file_digest(p) for p in (self.package / 'scene').iterdir()})
        write_json(self.package / 'manifest.json', self.manifest)
        write_json(self.package / 'task.json', self.task)
        write_json(self.package / 'stations/candidates.json', [{k: r[k] for k in ('station_id', 'xy', 'grid_index')} for r in self.rows])
        write_json(self.package / 'stations/validated.json', self.rows)
        selected, selection = select_stations(self.rows, self.task['anchor_world'])
        write_json(self.package / 'stations/selected.json', selected)
        write_json(self.package / 'stations/selection.json', selection)
        write_json(self.package / 'validation/checks.json', {'valid': True})
        self.refresh()

    def tearDown(self):
        self.tmp.cleanup()

    def refresh(self):
        write_json(self.package / 'checksums.json', {str(p.relative_to(self.package)): file_digest(p)
            for p in self.package.rglob('*') if p.is_file() and p != self.package / 'checksums.json'})

    def test_same_package_for_different_backends(self):
        from types import SimpleNamespace
        a = NoEditConfig(radius_m=self.geometry.radius_m, spacing_m=self.geometry.spacing_m)
        # Reader accepts geometry from ANY consumer; runtime backend constraints
        # (e.g. native motors/image dimensions) are checked by that consumer.
        b = SimpleNamespace(**{**vars(a), 'operation_backend': 'another-policy', 'planner_backend': 'another-planner'})
        self.assertEqual(package_manifest(self.package, self.robot, a), package_manifest(self.package, self.robot, b))

    def test_sensor_robot_and_checksum_changes_rejected(self):
        with self.assertRaisesRegex(ValueError, 'robot configuration'):
            package_manifest(self.package, replace(self.robot, control_hz=10), self.geometry)
        with self.assertRaisesRegex(ValueError, 'geometry mismatch'):
            package_manifest(self.package, self.robot, replace(self.geometry, width=640))
        (self.package / 'scene/initial.npz').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            package_manifest(self.package)

    def test_selection_and_full_candidate_evidence_rejected(self):
        write_json(self.package / 'stations/selected.json', self.rows[:5]); self.refresh()
        with self.assertRaisesRegex(ValueError, 'selection'):
            package_manifest(self.package)
        write_json(self.package / 'stations/selected.json', self.rows)
        write_json(self.package / 'stations/validated.json', self.rows[:5]); self.refresh()
        with self.assertRaisesRegex(ValueError, 'all original'):
            package_manifest(self.package)

    def test_bind_resume_and_no_resampling(self):
        manifest = package_manifest(self.package, self.robot, self.geometry)
        scene = self.root / 'execution'; bind_package(scene, manifest); bind_package(scene, manifest)
        rows, record = package_stations(scene, self.task)
        self.assertEqual(rows, self.rows); self.assertFalse(record['lattice_regenerated'])
        with self.assertRaisesRegex(ValueError, 'identity'):
            package_stations(scene, {**self.task, 'task_id': 'another'})
        (scene / 'scene/model.mjb').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            bind_package(scene, manifest)

    def test_all_valid_mode_preserves_all_points(self):
        selected, selection = select_stations(self.rows, [0, 0, 1], 'all_valid', 2)
        self.assertEqual(selected, self.rows); self.assertEqual(selection['final_count'], 6)

    def test_collector_prepares_frozen_points_without_planner(self):
        from lastmile_dataflow.workflows.no_edit import prepare_execution_stations
        scene = self.root / 'execution'; bind_package(scene, package_manifest(self.package))
        config = NoEditConfig(scene_package=str(self.package), radius_m=self.geometry.radius_m, spacing_m=self.geometry.spacing_m)
        with patch('lastmile_dataflow.stations.no_edit_sampling.disk_samples', side_effect=AssertionError('resampled')):
            prepare_execution_stations(scene, {**self.task, 'output_name': 'cup'}, config)
        self.assertEqual(read_json(scene / 'tasks/cup/stations.json'), self.rows)
        self.assertEqual(read_json(scene / 'tasks/cup/station_input.json')['sample_id'], 'sample')

    def test_relative_input_and_mutually_exclusive_legacy(self):
        path = self.root / 'config.json'; write_json(path, {'scene_package': 'package'})
        self.assertEqual(load_no_edit_config(path).scene_package, str(self.package))
        with self.assertRaises(ValueError): NoEditConfig(scene_package='package', augmented_variant='variant')


if __name__ == '__main__':
    unittest.main()
