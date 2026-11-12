"""Native phase/lane identity, retries and fallback exclusion without GPU solves."""

from types import SimpleNamespace
import unittest
import torch

from lastmile_dataflow.planning.curobo.tracing import lane_success, solver_evidence, phase_evidence


def query(index, phase, values, fov=None):
    return {
        "query_index": index,
        "phase": phase,
        "lane_success": values,
        "fov_rejected_lanes": fov or [False] * len(values),
    }


def solve(index, phase, kind, values, count=3, query_index=None):
    return solver_evidence(
        SimpleNamespace(success=torch.tensor(values)),
        kind,
        phase,
        index if query_index is None else query_index,
        index,
        count,
    )


class PlanningEvidenceTests(unittest.TestCase):
    def test_lane_result_is_not_batch_any(self):
        self.assertEqual(
            lane_success(torch.tensor([[True, False], [False, False], [False, True]]), 3), [True, False, True]
        )
        self.assertEqual(lane_success(None, 3), [False, False, False])

    def test_failed_lane_cannot_claim_later_fallback_success(self):
        queries = [
            query(1, "goalset", [False, True, True]),
            query(2, "pregrasp", [True, False, True]),
            query(3, "grasp", [True, True, True]),
        ]
        solves = [
            solve(1, "goalset", "ik", [[False], [True], [True]]),
            solve(2, "pregrasp", "ik", [[True], [False], [True]]),
            solve(3, "grasp", "ik", [[True], [True], [True]]),
        ]
        first = phase_evidence(queries, solves, 0)
        self.assertEqual([r["status"] for r in first], ["failure", "not_applicable", "not_applicable"])
        second = phase_evidence(queries, solves, 1)
        self.assertEqual([r["status"] for r in second], ["success", "failure", "not_applicable"])
        self.assertEqual(second[1]["failure_component"], "ik")
        third = phase_evidence(queries, solves, 2)
        self.assertTrue(all(r["status"] == "success" for r in third))

    def test_solver_retries_keep_explicit_query_mapping_and_unknown_denominator(self):
        queries = [query(1, "goalset", [False, True, False])]
        solves = [
            solve(i, "goalset", kind, values, query_index=1)
            for i, kind, values in (
                (1, "ik", [[False], [False], [True]]),
                (2, "ik", [[True], [True], [False]]),
                (3, "trajopt", [[False], [True], [False]]),
                (4, "ik", [[False], [True], [False]]),
                (5, "trajopt", [[False], [False], [False]]),
            )
        ]
        failed = phase_evidence(queries, solves, 0)[0]
        self.assertTrue(failed["ik_found"])
        self.assertFalse(failed["trajopt_found"])
        self.assertEqual(failed["failure_component"], "trajopt")
        self.assertEqual(failed["solver_calls"], [1, 2, 3, 4, 5])
        self.assertIsNone(failed["ik_feasible_ratio"])
        self.assertEqual(solves[0]["returned_shape"], [3, 1])

    def test_fov_rejection_is_separate_from_trajopt_failure(self):
        queries = [query(1, "goalset", [False], [True])]
        solves = [
            solve(1, "goalset", "ik", [[True]], count=1),
            solve(2, "goalset", "trajopt", [[True]], count=1, query_index=1),
        ]
        observed = phase_evidence(queries, solves, 0)[0]
        self.assertEqual(observed["failure_component"], "fov")
        self.assertTrue(observed["trajopt_found"])

    def test_missing_solver_observation_remains_unknown(self):
        observed = phase_evidence([query(1, "goalset", [False])], [], 0)[0]
        self.assertEqual(observed["failure_component"], "unknown")
        self.assertIsNone(observed["ik_found"])
