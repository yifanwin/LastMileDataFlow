"""Candidate coverage, native curve preservation and trajectory score semantics."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np
from scipy.interpolate import CubicSpline

from lastmile_dataflow.config import BaseMapConfig
from lastmile_dataflow.planning.curobo.planner import native_spline_curve
from lastmile_dataflow.planning.trajectory_quality import (
    JointCurve, TrajectoryCandidate, joint_distance, planning_rate, ranked_candidates,
    representatives, score_trajectory, terminal_clusters, trajectory_seeds)


def straight_curve(end=1., duration=2.):
    q = np.array([[0.]*7, [end]+[0.]*6])
    velocity = np.array([[end/duration]+[0.]*6]*2)
    return JointCurve([0, duration], q, velocity, np.zeros((2, 7)))


def trace_for(curve):
    time = np.linspace(curve.time[0], curve.time[-1], 201)
    q = curve.at(time)
    s = np.r_[0., np.cumsum(np.linalg.norm(np.diff(q, axis=0), axis=1))]
    tcp = np.tile(np.eye(4), (len(time), 1, 1))
    tcp[:, 0, 3] = q[:, 0]
    return dict(time=time, s=s, q=q, tcp=tcp, clearance=np.ones(len(time)), margin=np.ones(len(time)))


def evaluation(function):
    def evaluate(q):
        tcp = np.eye(4)
        tcp[0, 3] = q[0]
        return dict(quality=float(function(q[0])), clearance=1., margin=.5, tcp=tcp)
    return evaluate


class CandidateTests(unittest.TestCase):
    def test_normalized_farthest_first_and_diameter_clustering(self):
        span = np.array([2., 4., 1., 1., 1., 1., 1.])
        q = np.zeros((4, 7))
        q[:, 0] = [0, .08, .16, .4]
        self.assertAlmostEqual(joint_distance(q[0], q[1], span), .04)
        self.assertEqual(representatives(q, span, .05, 4), [0, 3, 2])
        labels, clusters = terminal_clusters(q[:3], span, .05)
        self.assertEqual(clusters, [[0, 1], [2]])  # no transitive chaining
        np.testing.assert_array_equal(labels, [0, 0, 1])

    def test_seeds_share_endpoints_and_history_is_corrected(self):
        q0, q1 = np.zeros(7), np.ones(7)
        bounds = (np.full(7, -2.), np.full(7, 2.))
        linear, perturbed = trajectory_seeds(q0, q1, 16, *bounds, np.random.default_rng(7), .03)
        np.testing.assert_equal(perturbed[[0, -1]], [q0, q1])
        self.assertGreater(np.max(np.abs(linear-perturbed)), .01)
        _, adapted = trajectory_seeds(q0, q1, 16, *bounds, np.random.default_rng(7), .03, linear+.2)
        np.testing.assert_allclose(adapted[[0, -1]], [q0, q1], atol=1e-15)

    def test_rate_precedes_retention_and_incomplete_has_no_official_rate(self):
        self.assertEqual(planning_rate(8, 8, 6), .75)
        self.assertIsNone(planning_rate(8, 7, 6))
        self.assertIsNone(planning_rate(8, 8, 6, incomplete=True))
        self.assertIsNone(planning_rate(0, 0, 0))

    def test_retention_uses_quality_then_length_then_id_and_no_region_quota(self):
        rows = [dict(candidate=TrajectoryCandidate(i, i//2, np.zeros(7), 'linear'),
                     quality=SimpleNamespace(full=q, length=length), terminal_cluster=0)
                for i, (q, length) in enumerate([(1., 3.), (2., 9.), (2., 8.), (2., 8.), (.5, 1.), (1.5, 2.)])]
        self.assertEqual([row['candidate'].candidate_id for row in ranked_candidates(rows)], [2, 3, 1, 5])
        self.assertEqual(len({row['terminal_cluster'] for row in ranked_candidates(rows)}), 1)


class CurveTests(unittest.TestCase):
    def test_native_curve_decoding_preserves_cubic_interiors_and_derivatives(self):
        native = CubicSpline(np.arange(9), np.random.default_rng(3).normal(size=(9, 7)))
        t = np.linspace(0, 8, 65)
        curve = native_spline_curve(native(t), native(t, 1), native(t, 2), .125, 4, 3)
        dense = np.linspace(0, 8, 1001)
        for derivative in (0, 1, 2):
            np.testing.assert_allclose(curve.at(dense, derivative), native(dense, derivative), atol=1e-10)

    def test_decoder_rejects_output_that_is_not_the_declared_spline(self):
        t = np.linspace(0, 8, 65)
        q = np.tile(np.sin(5*t)[:, None], (1, 7))
        with self.assertRaisesRegex(ValueError, 'differs'):
            native_spline_curve(q, 5*np.tile(np.cos(5*t)[:, None], (1, 7)), -25*q, .125, 4, 3)


class ScoringTests(unittest.TestCase):
    def test_common_start_bottleneck_post_boundary_and_nested_minima(self):
        curve = straight_curve()
        result = score_trajectory(curve, trace_for(curve), evaluation(lambda x: x), BaseMapConfig(), 1.7)
        self.assertEqual(len(result.samples), 32)
        self.assertEqual(result.full, 0.)
        self.assertAlmostEqual(result.post, .1)
        self.assertEqual(result.full_bottleneck['time'], 0.)
        self.assertEqual(result.full_bottleneck['phase'], 'initial_transition')
        self.assertEqual(result.samples[-1]['phase'], 'approach')
        self.assertTrue(result.samples[-1]['is_terminal'])
        self.assertTrue(all(a['full'] >= b['full'] for a, b in zip(result.rounds, result.rounds[1:])))
        self.assertTrue(all(r['post'] >= r['full'] for r in result.rounds))
        self.assertTrue(any(abs(s['u']-.1) < 1e-10 for s in result.samples))

    def test_refinement_finds_valley_and_static_path_scores_once(self):
        curve = straight_curve()
        result = score_trajectory(curve, trace_for(curve), evaluation(lambda x: (x-.5)**2), BaseMapConfig(), 1.7)
        self.assertLess(result.full, 1e-4)
        static = straight_curve(0.)
        result = score_trajectory(static, trace_for(static), evaluation(lambda x: .2), BaseMapConfig(), 0.)
        self.assertEqual(len(result.samples), 1)
        self.assertIsNone(result.post)
        self.assertEqual(result.full, .2)

    def test_longer_path_can_win_on_bottleneck(self):
        config = BaseMapConfig()
        short, long = straight_curve(), straight_curve(2.)
        a = score_trajectory(short, trace_for(short), evaluation(lambda x: .1), config, 1.8)
        b = score_trajectory(long, trace_for(long), evaluation(lambda x: .2), config, 1.8)
        rows = [dict(candidate=TrajectoryCandidate(i, i, np.zeros(7), 'linear'), quality=q)
                for i, q in enumerate((a, b))]
        self.assertGreater(b.length, a.length)
        self.assertEqual(ranked_candidates(rows)[0]['candidate'].candidate_id, 1)


if __name__ == '__main__':
    unittest.main()
