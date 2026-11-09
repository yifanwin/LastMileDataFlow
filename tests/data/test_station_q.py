import unittest

from lastmile_dataflow.data.evaluation.station_q import select_acceptance_stations, summarize_trials
from lastmile_dataflow.scenes.stations.rl import spatial_split


class StationQTests(unittest.TestCase):
    def test_predeclared_groups_are_disjoint_and_held_out(self):
        rows = [{'station_id': str(i), 'valid': i != 0, 'split': 'train' if i == 1 else 'acceptance',
                 'q_mean': float(i)} for i in range(40)]
        config = {'seed': 42, 'top': 4, 'middle': 4, 'low': 4, 'uniform': 8}
        selection = select_acceptance_stations(rows, config)
        self.assertEqual(len(selection), 20)
        self.assertEqual(len({r['station_id'] for r in selection}), 20)
        self.assertEqual([r['station_id'] for r in selection[:4]], ['39', '38', '37', '36'])
        self.assertTrue(all(r['valid'] and r['split'] == 'acceptance' for r in selection))
        self.assertEqual(selection, select_acceptance_stations(rows, config))

    def test_spatial_block_split_never_splits_yaws_or_same_block(self):
        points = [{'xy': [i*.1, j*.1]} for i in range(20) for j in range(20)]
        labels = spatial_split(points, .3, [.7, .15, .15], 42)
        self.assertEqual(set(labels), {'train', 'validation', 'acceptance'})
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels, spatial_split(points, .3, [.7, .15, .15], 42))

    def test_infrastructure_failures_remain_in_total_denominator(self):
        selection = [{'station_id': 's1', 'group': 'top', 'q_mean': 1.}]
        trials = [{'station_id': 's1', 'success': True, 'collision_free': True,
                   'infrastructure_failure': False, 'return': 20.},
                  {'station_id': 's1', 'success': False, 'collision_free': False,
                   'infrastructure_failure': True, 'return': 0.}]
        summary = summarize_trials(selection, trials, seed=42)
        self.assertEqual(summary['infrastructure_failures'], 1)
        self.assertEqual(summary['stations'][0]['trials'], 2)
        self.assertEqual(summary['stations'][0]['healthy_trials'], 1)
        self.assertEqual(summary['stations'][0]['success_rate'], 1.)
        self.assertIsNone(summary['q_success_spearman'])
