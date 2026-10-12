"""Synthetic contract regressions; not a real ProcTHOR task-success claim."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.io import write_json, read_json, file_digest
from lastmile_dataflow.stations.no_edit_config import NoEditConfig, load_no_edit_config
from lastmile_dataflow.stations.obstacle_selection import coverage_select
from lastmile_dataflow.stations.augmented_input import variant_manifest, bind_scene_inputs, execution_stations


class AugmentedStationInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.run = self.root/'parent'
        self.variant = self.run/'variants/v000'; (self.variant/'scene').mkdir(parents=True)
        self.robot = RobotConfig(str(self.root/'robot.xml'))
        self.config = NoEditConfig()
        self.task = {'task_id':'pick-test', 'target_body':'cup', 'asset_id':'Cup_30', 'anchor_world':[0,0,1]}
        write_json(self.run/'baseline/task.json', self.task)
        write_json(self.run/'frozen_config.json', {'baseline':asdict(self.config),
                    'augmentation':{'construction':{'case':'Case1'}}})
        write_json(self.run/'summary.json', {'status':'completed'})
        write_json(self.variant/'checks.json', {'valid':True})
        write_json(self.variant/'scene/version.json', {'robot':asdict(self.robot),
                    'source':{'scene_id':'procthor-10k-val/val_103'}})
        for name in ('model.mjb','initial.npz','instances.json'):
            (self.variant/'scene'/name).write_bytes(b'fixture-not-a-real-snapshot')
        self.refresh_checksums()
        import math
        self.rows = [{'station_id':f'S{i:04d}', 'xy':[math.cos(i*.23)*(1+i/40), math.sin(i*.23)*(1+i/40)],
                      'geometry':'valid','independent_simulation':True,'non_robot_qpos_unchanged':True,
                      'initial':{'base':[math.cos(i*.23)*(1+i/40), math.sin(i*.23)*(1+i/40),0]},
                      'visibility':{'target':'cup'},'new_solver_warnings':[]} for i in range(35)]
        stations, selection = coverage_select(self.rows, self.task['anchor_world'],20)
        self.stations = stations
        write_json(self.variant/'stations_final.json', stations)
        write_json(self.variant/'station_selection.json', selection)
        write_json(self.variant/'navigation/comparison.json', {'stations_after':self.rows})

    def refresh_checksums(self):
        write_json(self.variant/'scene/checksums.json', {n:file_digest(self.variant/'scene'/n)
                  for n in ('model.mjb','initial.npz','instances.json','version.json')})

    def manifest(self):
        return variant_manifest(self.variant, self.robot, self.config)

    def test_import_exact_coverage_set_and_resume(self):
        m = self.manifest(); scene = self.root/'new/scenes/val_103'
        bind_scene_inputs(scene,m); bind_scene_inputs(scene,m)
        rows, record = execution_stations(scene,self.task)
        self.assertEqual(rows,self.stations); self.assertEqual(len(rows),20)
        self.assertNotEqual([s['station_id'] for s in rows],[s['station_id'] for s in self.rows[:20]])
        self.assertFalse(record['lattice_regenerated'])
        self.assertEqual(file_digest(scene/'scene/model.mjb'),file_digest(self.variant/'scene/model.mjb'))

    def test_prefix_truncation_and_stale_selection_rejected(self):
        write_json(self.variant/'stations_final.json',self.rows[:20])
        with self.assertRaisesRegex(ValueError,'coverage'): self.manifest()

    def test_robot_and_geometry_config_mismatch_rejected(self):
        for r,c in [(replace(self.robot,control_hz=10),self.config),
                    (self.robot,replace(self.config,spacing_m=.1)),
                    (self.robot,replace(self.config,width=640))]:
            with self.assertRaises(ValueError): variant_manifest(self.variant,r,c)

    def test_checksum_damage_rejected(self):
        (self.variant/'scene/initial.npz').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checksum'): self.manifest()

    def test_existing_raw_run_not_retrofitted(self):
        m = self.manifest(); scene=self.root/'existing'; (scene/'scene').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'new run-id'): bind_scene_inputs(scene,m)

    def test_resume_tampering_and_target_mismatch_rejected(self):
        m = self.manifest(); scene=self.root/'new'; bind_scene_inputs(scene,m)
        with self.assertRaisesRegex(ValueError,'identity'): execution_stations(scene,{**self.task,'target_body':'other'})
        write_json(scene/'augmented_input/stations_final.json',self.stations[:19])
        with self.assertRaisesRegex(ValueError,'checksum'): bind_scene_inputs(scene,m)
        with self.assertRaisesRegex(ValueError,'checksum'): execution_stations(scene,self.task)

    def test_relative_config_path_resolved_and_legacy_optional(self):
        p=self.root/'config.json';write_json(p,{'augmented_variant':'parent/variants/v000'})
        self.assertEqual(load_no_edit_config(p).augmented_variant,str(self.variant))
        self.assertIsNone(NoEditConfig().augmented_variant)
        with self.assertRaises(ValueError): NoEditConfig(augmented_variant='')

    def test_collector_does_not_resample_or_render_initialization(self):
        from lastmile_dataflow.workflows.no_edit import collect_task
        from types import SimpleNamespace
        m=self.manifest();scene=self.root/'new';bind_scene_inputs(scene,m)
        write_json(scene/'robot_geometry.json',{'radius_m':.3})
        task={**self.task,'output_name':'cup2'}
        class StopAfterStations(Exception): pass
        with patch('lastmile_dataflow.stations.no_edit_sampling.disk_samples',side_effect=AssertionError('resampled')), \
             patch('lastmile_dataflow.stations.no_edit_sampling.initialize_station',side_effect=AssertionError('reinitialized')), \
             patch('mujoco.Renderer',side_effect=AssertionError('rendered')), \
             patch('lastmile_dataflow.navigation.astar.task_support_obstacles',return_value=[]), \
             patch('lastmile_dataflow.tasks.raw_scene.grasp_candidates',side_effect=StopAfterStations):
            with self.assertRaises(StopAfterStations):
                collect_task(None,task,self.root,replace(self.config,augmented_variant=str(self.variant)),None,scene)
        self.assertEqual(read_json(scene/'tasks/cup2/stations.json'),self.stations)
        self.assertEqual(read_json(scene/'tasks/cup2/station_input.json')['execution_point_count'],20)
        write_json(scene/'tasks/cup2/stations.json',self.rows)
        with self.assertRaisesRegex(ValueError,'cached stations'):
            collect_task(None,task,self.root,replace(self.config,augmented_variant=str(self.variant)),None,scene)
