"""The mjlab control-step pick judgement equals the native RoboCasa PickJudge on the same fact sequences."""

from types import SimpleNamespace
import unittest

import numpy as np
import torch

from lastmile_dataflow.operation.outcomes.robocasa_pick import PickJudge
from lastmile_dataflow.station_map.rl.env.terms import TaskState

DT = 0.05  # 25 physics steps of 2 ms = one native 20 Hz control step


def native_step(facts):
    judge = PickJudge("obj", 0.0)
    for k, (grasped, unsupported, lifted) in enumerate(facts):
        state = SimpleNamespace(target="obj", time_s=(k + 1) * DT, target_bottom_z=0.03 if lifted else 0.01,
                                grasped_objects=("obj",) if grasped else (), target_supported=not unsupported)
        if judge.update(state).success:
            return k
    return None


def mjlab_step(facts):
    zeros = lambda dtype: torch.zeros(1, dtype=dtype)
    task = SimpleNamespace(pick_facts=zeros(torch.bool), pick_start=torch.full((1,), -1.0, dtype=torch.float64),
                           hold=zeros(torch.float64), elapsed=zeros(torch.float64), success=zeros(torch.bool),
                           failed=zeros(torch.bool), unhealthy=zeros(torch.bool))
    for k, (grasped, unsupported, lifted) in enumerate(facts):
        for _ in range(25):
            task.elapsed += 0.002
        task.pick_facts[:] = grasped and unsupported and lifted
        TaskState.judge_pick(task)
        if task.success.item():
            return k
    return None


class NativePickAlignmentTest(unittest.TestCase):
    def test_hold_starts_and_latches_on_the_same_step(self):
        held = [(True, True, True)] * 12
        self.assertEqual(native_step(held), 10)
        self.assertEqual(mjlab_step(held), 10)

    def test_broken_fact_restarts_the_window(self):
        facts = [(True, True, True)] * 8 + [(True, False, True)] + [(True, True, True)] * 12
        self.assertEqual(mjlab_step(facts), native_step(facts))
        self.assertEqual(native_step(facts), 19)

    def test_random_sequences(self):
        rng = np.random.default_rng(0)
        for _ in range(200):
            facts = [tuple(bool(x) for x in row) for row in rng.random((60, 3)) < [0.97, 0.97, 0.97]]
            self.assertEqual(mjlab_step(facts), native_step(facts))


if __name__ == "__main__":
    unittest.main()
