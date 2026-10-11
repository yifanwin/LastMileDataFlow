"""Preset regression only; real model geometry is verified by the explicit script."""
from dataclasses import asdict
from pathlib import Path
import unittest

import numpy as np

from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.stations.no_edit_sampling import disk_samples


ROOT = Path(__file__).resolve().parents[1]


class ReachSamplingPresetTests(unittest.TestCase):
    def test_only_requested_sampling_fields_differ_from_v080(self):
        base = asdict(load_no_edit_config(ROOT / 'configs/no_edit/curobo_v080.json'))
        preset = asdict(load_no_edit_config(
            ROOT / 'configs/no_edit/curobo_v080_arm_reach_half_base_radius.json'))
        self.assertEqual({k for k in base if base[k] != preset[k]}, {'radius_m', 'spacing_m'})
        self.assertEqual(preset['planner_backend'], 'curobo_v2_v080')
        self.assertEqual(preset['trials_per_station'], 5)
        self.assertAlmostEqual(preset['radius_m'], 0.7928056080178096, places=12)
        self.assertEqual(preset['spacing_m'], 0.4435184160005256 / 4)

    def test_preset_disk_extent_and_quarter_radius_lattice(self):
        c = load_no_edit_config(ROOT / 'configs/no_edit/curobo_v080_arm_reach_half_base_radius.json')
        anchor = np.array([7.3134564528010175, 2.098837342592207])
        rows = disk_samples(anchor, c.radius_m, c.spacing_m)
        self.assertEqual(len(rows), 161)
        for row in rows:
            offset = np.asarray(row['xy']) - anchor
            self.assertLessEqual(np.linalg.norm(offset), c.radius_m + 1e-12)
            np.testing.assert_allclose(offset, np.asarray(row['grid_index']) * c.spacing_m, atol=1e-12)


if __name__ == '__main__':
    unittest.main()
