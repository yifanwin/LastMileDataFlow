import unittest
import numpy as np
from lastmile_dataflow.station_map.robocasa_metrics import auroc, paired_bootstrap, rank_metrics


class RobocasaMetricsTest(unittest.TestCase):
    def test_perfect_ranking(self):
        s = np.linspace(0, 1, 20)
        m = rank_metrics(s * 3, s)
        self.assertAlmostEqual(m.spearman, 1.0)
        self.assertAlmostEqual(m.kendall_tau_b, 1.0)
        self.assertAlmostEqual(m.regret, 0.0)
        self.assertAlmostEqual(m.auroc, 1.0)

    def test_ties_at_top_share_slots(self):
        m = rank_metrics(np.array([1.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.5]))
        self.assertAlmostEqual(m.top1, 0.5)
        self.assertAlmostEqual(m.regret, 0.5)

    def test_auroc_single_class(self):
        self.assertIsNone(auroc(np.arange(4.0), np.ones(4)))

    def test_bootstrap_detects_better_score(self):
        rng = np.random.default_rng(0)
        latent = rng.uniform(size=60)
        s = latent
        result = paired_bootstrap(latent + rng.normal(0, 0.05, 60), rng.uniform(size=60), s, reps=500)
        self.assertTrue(result["significant"])


if __name__ == "__main__":
    unittest.main()
