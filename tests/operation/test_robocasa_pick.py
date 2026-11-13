"""robocasa-pick-1cm-1s-v1 judgement on synthetic runtime facts (no RoboCasa needed)."""

import unittest

import numpy as np

from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL, PickJudge
from lastmile_dataflow.sim.runtime import RuntimeState

Z0 = 0.90


def state(t, *, z=Z0 + 0.02, grasped=("obj",), supported=False, target="obj"):
    return RuntimeState(
        time_s=t,
        robot={},
        target=target,
        target_pose=np.r_[0.0, 0.0, z, 1.0, 0.0, 0.0, 0.0],
        grasped_objects=grasped,
        target_supported=supported,
    )


def run(facts):
    judge = PickJudge("obj", Z0)
    return [judge.update(f) for f in facts]


STEPS = [round(0.05 * i, 2) for i in range(31)]  # 20 Hz control steps over 1.5 s


class RoboCasaPickTests(unittest.TestCase):
    def test_protocol_is_separate_from_strict_pick(self):
        self.assertEqual(PICK_PROTOCOL["version"], "robocasa-pick-1cm-1s-v1")
        self.assertEqual((PICK_PROTOCOL["lift_m"], PICK_PROTOCOL["hold_s"]), (0.01, 1.0))

    def test_all_conditions_held_for_one_second_succeeds(self):
        statuses = run([state(t) for t in STEPS])
        first = next(s for s in statuses if s.success)
        self.assertAlmostEqual(first.time_s, 1.0)
        self.assertAlmostEqual(first.lift_m, 0.02)
        self.assertTrue(statuses[-1].success)

    def test_insufficient_lift(self):
        statuses = run([state(t, z=Z0 + 0.009) for t in STEPS])
        self.assertFalse(any(s.success for s in statuses))
        self.assertFalse(statuses[-1].lifted)

    def test_insufficient_hold(self):
        statuses = run([state(t) for t in STEPS if t < 0.95])
        self.assertFalse(statuses[-1].success)
        self.assertAlmostEqual(statuses[-1].hold_s, 0.9)

    def test_interrupted_grasp_restarts_window(self):
        facts = [state(t, grasped=() if t == 0.5 else ("obj",)) for t in STEPS]
        statuses = run(facts)
        self.assertEqual(statuses[10].hold_s, 0.0)
        self.assertFalse(any(s.success for s in statuses))  # window restarts at 0.55 s; 1.5 s is only 0.95 s later
        longer = run(facts + [state(1.55)])
        self.assertTrue(longer[-1].success)

    def test_supported_target_never_succeeds(self):
        statuses = run([state(t, supported=True) for t in STEPS])
        self.assertFalse(any(s.success for s in statuses))
        self.assertFalse(statuses[-1].unsupported)

    def test_grasping_another_object_does_not_count(self):
        statuses = run([state(t, grasped=("distr_counter",)) for t in STEPS])
        self.assertFalse(any(s.success for s in statuses))
        with self.assertRaises(ValueError):
            PickJudge("obj", Z0).update(state(0.0, target="distr_counter"))


if __name__ == "__main__":
    unittest.main()
