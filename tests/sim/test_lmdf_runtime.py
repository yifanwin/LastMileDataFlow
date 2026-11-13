"""LMDF runtime wrapper over a synthetic library snapshot: reset, step, station and facts."""

from unittest.mock import patch

import numpy as np

from lastmile_dataflow.robots.interface import RobotCommand
from lastmile_dataflow.robots.rby1.adapter import RBY1Adapter
from lastmile_dataflow.scenes.library import load_scenes
from lastmile_dataflow.sim.lmdf_runtime import LmdfRuntime
from tests.helpers import DatasetTestCase


class LmdfRuntimeTests(DatasetTestCase):
    # The synthetic fixture has no ee_finger bodies; grasp facts are covered on real models.
    @patch.object(RBY1Adapter, "finger_bodies", lambda self, arm: (set(), set()))
    def test_reset_restores_snapshot_and_station_moves_only_the_base(self):
        record = load_scenes(self.dataset())[0]
        runtime = LmdfRuntime(record["scene_path"], self.robot, target="target")
        try:
            self.assertEqual(runtime.action_spec.runtime, "lmdf")
            initial = runtime.get_state()
            self.assertEqual(initial.target, "target")
            self.assertEqual(initial.grasped_objects, ())
            np.testing.assert_array_equal(runtime.sim.state_vector(), self.sim.state_vector())
            runtime.step(RobotCommand())  # hold current targets
            self.assertGreater(runtime.get_state().time_s, initial.time_s)
            runtime.reset()
            np.testing.assert_array_equal(runtime.sim.state_vector(), self.sim.state_vector())
            moved = runtime.set_station(0.4, 0.0, 0.0)
            np.testing.assert_allclose(runtime.sim.robot.base_pose(), [0.4, 0.0, 0.0], atol=1e-9)
            self.assertFalse(runtime.sim.started)
            np.testing.assert_array_equal(moved.target_pose, initial.target_pose)
        finally:
            runtime.close()
