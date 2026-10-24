"""Builder saves the real no-addition control and its independently checked stations."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np

from lastmile_dataflow.scenes.augmentation import builder


class BuilderPreviewTests(unittest.TestCase):
    def test_accepted_variant_saves_both_snapshots_and_uses_same_lattice(self):
        policy = SimpleNamespace(station_selection='coverage', max_final_stations=20, min_final_points=1, case='Case1')
        config = SimpleNamespace(case_policy=policy, variant_timeout_s=30, obstacle_count=[1, 1],
                                 mandatory_categories=[], max_candidates=1, placement_gap_m=.01,
                                 max_physical_trials=1, record_views=False)
        after, before, session = Mock(), Mock(), Mock()
        after.model.body.return_value.id = 0
        after.data.xpos = np.array([[0, 0, 1]])
        after.data.xquat = np.array([[1, 0, 0, 0]])
        session.candidate.return_value = after
        session.settle.return_value = {'simulated_s': 1.0}
        region = SimpleNamespace(region_id='support', bounds=[0, 1, 0, 1])
        surfaces = [{'xy': [0, 0], 'region': region}]
        task = {'anchor_world': [0, 0, 1]}
        lattice = [{'station_id': 'S0000', 'xy': [0, 0], 'grid_index': [0, 0]}]
        pre = [{**lattice[0], 'geometry': 'valid'}]
        post = [{**lattice[0], 'geometry': 'valid', 'base': [0, 0, 1]}]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(builder, 'surface_occupied_bounds', return_value=[]), \
             patch.object(builder, 'case_assets', return_value=[{'category': 'Cup'}]), \
             patch.object(builder, 'sample_surface_point', return_value=(surfaces[0], 0)), \
             patch.object(builder, 'sample_placement', return_value=({}, None)), \
             patch.object(builder, 'collision_pairs', return_value={'valid': True}), \
             patch.object(builder, 'validate_augmented_scene', return_value={'valid': True}), \
             patch.object(builder, 'matched_control', return_value=(before, {})), \
             patch.object(builder, 'record_station_visibility') as visibility, \
             patch.object(builder, 'sample_stations', side_effect=[post, pre]) as sample:
            path = Path(directory) / 'variant'
            result = builder.build_variant(session, Mock(), task, config,
                                           SimpleNamespace(require_target_visibility=False), {'ground': []},
                                           [], {}, surfaces, lattice, path, 'variant', 1)
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(result['before_stations'], pre)
            self.assertEqual(result['stations'], post)
            self.assertEqual(visibility.call_count, 2)
            after.freeze.assert_called_once_with(path / 'scene')
            before.freeze.assert_called_once_with(path / 'before-scene')
            self.assertIs(sample.call_args_list[0].kwargs['frozen_stations'], lattice)
            self.assertIs(sample.call_args_list[1].kwargs['frozen_stations'], lattice)
            before.close.assert_called_once()
