import unittest
import numpy as np
from lastmile_dataflow.construction.sampling import sample_xy, sample_scalar, SamplingRNG


class SamplingTests(unittest.TestCase):
    def test_replay_and_full_coverage(self):
        def samples():
            rng = np.random.default_rng(7)
            return [sample_xy([-10, 10, -5, 5], rng, index=i, count=20, selection='coverage') for i in range(20)]
        self.assertEqual(samples(), samples())
        self.assertLess(min(x[0] for x in samples()), -9)
        self.assertGreater(max(x[0] for x in samples()), 9)

    def test_grid_and_empty_interval(self):
        rng = np.random.default_rng(1)
        self.assertEqual(sample_xy([0, 2, 0, 2], rng, mode='grid', index=8, steps=3), [2., 2.])
        with self.assertRaises(ValueError):
            sample_xy([2, 1, 0, 1], rng)

    def test_joint_coverage_does_not_correlate_two_angle_parameters(self):
        def samples():
            result = []
            for index in range(16):
                rng = SamplingRNG(42, index)
                result.append([sample_scalar([0,1], rng, index, 16, 'coverage') for _ in range(2)])
            return np.array(result)
        a = samples()
        np.testing.assert_array_equal(a, samples())
        for dimension in range(2):
            self.assertEqual(sorted((a[:,dimension]*16).astype(int)), list(range(16)))
        self.assertGreater(np.std(a[:,0]-a[:,1]), .15)
