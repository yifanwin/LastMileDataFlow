"""robocasa-pick-bottom2cm-0.5s-v2 judgement on synthetic runtime facts (no RoboCasa needed)."""

import unittest

import numpy as np

from lastmile_dataflow.operation.outcomes.robocasa_pick import PICK_PROTOCOL, PickJudge
from lastmile_dataflow.sim.runtime import RuntimeState

TABLE = 0.90


def state(t, *, bottom=TABLE + 0.03, grasped=("obj",), supported=False, target="obj"):
    return RuntimeState(
        time_s=t,
        robot={},
        target=target,
        target_pose=np.r_[0.0, 0.0, TABLE + 0.5, 1.0, 0.0, 0.0, 0.0],  # origin height must not matter
        grasped_objects=grasped,
        target_supported=supported,
        target_bottom_z=bottom,
    )


def run(facts):
    judge = PickJudge("obj", TABLE)
    return [judge.update(f) for f in facts]


STEPS = [round(0.05 * i, 2) for i in range(31)]  # 20 Hz control steps over 1.5 s


class RoboCasaPickTests(unittest.TestCase):
    def test_protocol(self):
        self.assertEqual(PICK_PROTOCOL["version"], "robocasa-pick-bottom2cm-0.5s-v2")
        self.assertEqual((PICK_PROTOCOL["lift_m"], PICK_PROTOCOL["hold_s"]), (0.02, 0.5))

    def test_all_conditions_held_for_half_second_succeeds(self):
        statuses = run([state(t) for t in STEPS])
        first = next(s for s in statuses if s.success)
        self.assertAlmostEqual(first.time_s, 0.5)
        self.assertAlmostEqual(first.lift_m, 0.03)
        self.assertTrue(statuses[-1].success)

    def test_lift_uses_bottom_not_origin(self):
        statuses = run([state(t, bottom=TABLE + 0.019) for t in STEPS])
        self.assertFalse(any(s.success for s in statuses))
        self.assertFalse(statuses[-1].lifted)

    def test_insufficient_hold(self):
        statuses = run([state(t) for t in STEPS if t < 0.45])
        self.assertFalse(statuses[-1].success)
        self.assertAlmostEqual(statuses[-1].hold_s, 0.4)

    def test_interrupted_grasp_restarts_window(self):
        statuses = run([state(t, grasped=() if t == 0.3 else ("obj",)) for t in STEPS])
        self.assertEqual(statuses[6].hold_s, 0.0)
        first = next(s for s in statuses if s.success)
        self.assertAlmostEqual(first.time_s, 0.85)  # window restarts at 0.35 s

    def test_supported_target_never_succeeds(self):
        statuses = run([state(t, supported=True) for t in STEPS])
        self.assertFalse(any(s.success for s in statuses))
        self.assertFalse(statuses[-1].unsupported)

    def test_grasping_another_object_does_not_count(self):
        statuses = run([state(t, grasped=("distr_counter",)) for t in STEPS])
        self.assertFalse(any(s.success for s in statuses))
        with self.assertRaises(ValueError):
            PickJudge("obj", TABLE).update(state(0.0, target="distr_counter"))


if __name__ == "__main__":
    unittest.main()
