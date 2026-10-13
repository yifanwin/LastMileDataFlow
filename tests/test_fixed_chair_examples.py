"""Anchor math and direct output contracts; not real construction evidence."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from lastmile_dataflow.construction.fixed_chair import validate_fixed_chair, fixed_chair_point, check_fixed_chair
from lastmile_dataflow.construction.obstacle_regions import rotated_placement
from lastmile_dataflow.construction.scene_dataset_config import SceneDatasetRecipe
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.workflows.lastmile_datasets import collect


ANCHOR = {'asset_id':'Chair_007_2', 'root_xy':[6.9,2.1],
          'position_tolerance_m':.006, 'source_model_sha256':'a'*64}


class FixedChairTests(unittest.TestCase):
    def test_rotation_never_moves_off_center_root_xy(self):
        local = np.array([[1.,2.,-.2],[3.,4.,.8]])
        for yaw in np.linspace(-np.pi,np.pi,17):
            point = fixed_chair_point(local,ANCHOR['root_xy'],yaw)
            pose,_ = rotated_placement(local,point['xy'],yaw,0.,.003)
            np.testing.assert_allclose(pose['position'][:2],ANCHOR['root_xy'],atol=1e-14,rtol=0)

    def test_constraint_rejects_wrong_asset_or_drift(self):
        sim=SimpleNamespace(model=SimpleNamespace(body=lambda name:SimpleNamespace(id=0)),
                            data=SimpleNamespace(xpos=np.array([[6.9,2.1,.5]])))
        row={'kind':'ground','category':'Chair','asset_id':'Chair_007_2','pose':{'position':[6.9,2.1,.5]}}
        self.assertTrue(check_fixed_chair(sim,[row],ANCHOR)['valid'])
        self.assertFalse(check_fixed_chair(sim,[dict(row,asset_id='other')],ANCHOR)['valid'])
        sim.data.xpos[0,0]+=.007
        self.assertFalse(check_fixed_chair(sim,[row],ANCHOR)['valid'])

    def test_bad_anchor_and_case_refused(self):
        validate_fixed_chair(ANCHOR)
        for bad in ([], {'asset_id':'Chair'},dict(ANCHOR,root_xy=[float('nan'),0]),dict(ANCHOR,position_tolerance_m=.01)):
            with self.assertRaises(ValueError): validate_fixed_chair(bad)
        recipe=SceneDatasetRecipe('data','assets','robot',[103],cases=['Case1'],fixed_chair=ANCHOR)
        with self.assertRaises(ValueError): replace(recipe,cases=['Case2'])

    def test_direct_publication_keeps_sample_execution_inside_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d); root=out/'examples/lastmile-datasets'
            write_json(out/'scene/manifest.json', {'status':'sealed'})
            units=[{'sample_id':f's{i}','unit_id':f'u{i}','selected_points':20} for i in range(3)]
            config=Path(__file__).resolve().parents[1]/'configs/lastmile_datasets/v0.json'
            with patch('lastmile_dataflow.workflows.lastmile_datasets.input_units',return_value=({'status':'sealed'},units)), \
                 patch('lastmile_dataflow.workflows.lastmile_datasets.code_identity',return_value={'code_sha256':'fixture'}):
                actual=collect(out/'scene',out/'assets',config,out,run_id='fixture',gpu_ids=[0],
                               preflight_only=True,publication_root=root)
            self.assertEqual(actual,root)
            for unit in read_json(root/'run.json')['units']:
                self.assertEqual(Path(unit['run_path']),root/'samples'/unit['sample_id'])
            self.assertEqual(read_json(root/'summary.json')['selected_points'],60)
