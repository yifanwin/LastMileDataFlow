"""Actual MuJoCo segment collision, contact windows and analytic motion limits."""
from types import SimpleNamespace
import unittest

import mujoco
import numpy as np

from lastmile_dataflow.config import BaseMapConfig
from lastmile_dataflow.planning.trajectory_quality import JointCurve
from lastmile_dataflow.planning.trajectory_validation import TrajectoryValidator, approach_segment


def moving_sphere(wall=0.):
    model = mujoco.MjModel.from_xml_string(f'''<mujoco><worldbody>
      <body name="robot_0/base"><joint type="hinge" axis="0 0 1"/>
      <geom size=".001" mass="1" contype="0" conaffinity="0"/>
      <body name="robot_0/arm"><joint type="slide" axis="1 0 0" range="-2 2"/>
        <geom name="robot_0/tool" type="sphere" size=".03"/>
        <geom name="robot_0/pad1" type="sphere" size=".001" contype="0" conaffinity="0"/>
        <geom name="robot_0/pad2" type="sphere" size=".001" contype="0" conaffinity="0"/>
        <site name="tcp"/></body></body>
      <body name="target" pos="1.5 0 0"><geom type="sphere" size=".03"/></body>
      <geom name="wall" type="sphere" size=".03" pos="{wall} 0 0"/>
    </worldbody></mujoco>''')
    data = mujoco.MjData(model)
    robot = SimpleNamespace(config=SimpleNamespace(namespace='robot_0/'), data=data, tcp_site=lambda side: 0)
    sim = SimpleNamespace(model=model, data=data, robot=robot, target_id=model.body('target').id)
    scoring = SimpleNamespace(qpos=np.array([1]), lower=np.array([-2.]), upper=np.array([2.]), self_pairs=[])
    validator = TrajectoryValidator(sim, scoring, BaseMapConfig(contact_pad_names=('pad1', 'pad2')))
    return sim, validator


class ValidationTests(unittest.TestCase):
    def test_internal_collision_is_rejected_although_both_endpoints_are_clear(self):
        sim, validator = moving_sphere()
        curve = JointCurve([0., 2.], [[-.5], [.5]], [[.5], [.5]], [[0.], [0.]])
        for q in ([-.5], [.5]):
            validator.set_q(q)
            self.assertTrue(validator.geometry()['valid'])
        # Query's goal pose does not exempt this non-target wall.
        goal = np.eye(4)
        goal[0, 3] = .5
        result = validator.validate(curve, [-.5], goal, np.array([2.]), np.array([5.]))
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, 'collision')
        self.assertGreater(result.checked_states, 16)
        self.assertGreater(result.failure_time, 0.)
        self.assertLess(result.failure_time, 2.)

    def test_position_only_update_matches_full_forward_geometry_contacts_and_jacobian(self):
        sim, validator = moving_sphere(.5)
        validator.set_q([.5])
        m, d = sim.model, sim.data
        positions, rotations = d.geom_xpos.copy(), d.geom_xmat.copy()
        contacts = [(int(c.geom1), int(c.geom2), float(c.dist)) for c in d.contact[:d.ncon]]
        jacp, jacr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
        mujoco.mj_jacSite(m, d, jacp, jacr, 0)
        mujoco.mj_forward(m, d)
        np.testing.assert_allclose(d.geom_xpos, positions)
        np.testing.assert_allclose(d.geom_xmat, rotations)
        self.assertEqual(contacts, [(int(c.geom1), int(c.geom2), float(c.dist)) for c in d.contact[:d.ncon]])
        fullp, fullr = np.zeros_like(jacp), np.zeros_like(jacr)
        mujoco.mj_jacSite(m, d, fullp, fullr, 0)
        np.testing.assert_allclose(fullp, jacp)
        np.testing.assert_allclose(fullr, jacr)

    def test_same_q_is_rechecked_after_world_station_change(self):
        sim, validator = moving_sphere(.5)
        sim.data.qpos[0] = np.pi/2
        validator.set_q([.5])
        self.assertTrue(validator.geometry()['valid'])
        sim.data.qpos[0] = 0.
        validator.set_q([.5])
        self.assertFalse(validator.geometry()['valid'])

    def test_only_pad_target_terminal_window_is_allowed(self):
        sim, validator = moving_sphere()
        pad = sim.model.geom('robot_0/pad1').id
        tool = sim.model.geom('robot_0/tool').id
        target = int(sim.model.body_geomadr[sim.target_id])
        self.assertIsNone(validator._allowed(pad, target, False))
        self.assertEqual(validator._allowed(pad, target, True), 'pad')
        self.assertIsNone(validator._allowed(tool, target, True))
        self.assertIsNone(validator._allowed(pad, sim.model.geom('wall').id, True))

    def test_analytic_velocity_extremum_between_knots_is_checked(self):
        _, validator = moving_sphere()
        curve = JointCurve([0., 1.], [[-.5], [.5]], [[0.], [0.]], [[0.], [0.]])
        self.assertEqual(validator._kinematic_failure(curve, [-.5], np.array([1.]), np.array([100.])), 'velocity_limit')
        self.assertEqual(validator._kinematic_failure(curve, [0.], np.array([100.]), np.array([100.])), 'start_mismatch')

    def test_approach_direction_and_shorter_than_soft_target(self):
        config, goal = BaseMapConfig(), np.eye(4)
        tcp = np.tile(np.eye(4), (21, 1, 1))
        tcp[:, 2, 3] = np.linspace(-.03, 0, 21)
        start, length = approach_segment(tcp, goal, config)
        self.assertEqual(start, 0)
        self.assertGreater(length, config.minimum_approach_m)
        self.assertLess(length, config.approach_distance_m)
        tcp[:, 2, 3] *= -1
        _, length = approach_segment(tcp, goal, config)
        self.assertLess(length, config.minimum_approach_m)

    def test_early_pregrasp_then_wrong_final_approach_is_rejected(self):
        tcp = np.tile(np.eye(4), (4, 1, 1))
        tcp[:, :3, 3] = [[0, 0, -.1], [.1, 0, -.05], [.02, 0, -.01], [0, 0, 0]]
        _, length = approach_segment(tcp, np.eye(4), BaseMapConfig())
        self.assertEqual(length, 0.)


    def test_shared_score_cache_keeps_goal_specific_hard_clearance(self):
        from unittest.mock import Mock
        from lastmile_dataflow.planning.trajectory_validation import TrajectoryStateScorer
        model = SimpleNamespace(lower=np.array([-1.]), upper=np.array([1.]),
                                score=Mock(return_value={'quality': .2}))
        validator = SimpleNamespace(model=model, sim=None, set_q=Mock(),
            geometry=Mock(side_effect=[dict(valid=True, tcp=np.eye(4), clearance=.1), dict(valid=True, tcp=np.eye(4), clearance=.2)]))
        cache = {}
        first = TrajectoryStateScorer(validator, np.eye(3), np.eye(4), cache)
        second = TrajectoryStateScorer(validator, np.eye(3), np.eye(4)*2, cache)
        self.assertEqual(first(np.array([0.]))['clearance'], .1)
        self.assertEqual(second(np.array([0.]))['clearance'], .2)
        self.assertEqual(model.score.call_count, 1)
        self.assertEqual(validator.geometry.call_count, 2)


    def test_touching_soft_pair_is_zero_quality_with_independent_hard_validity(self):
        from unittest.mock import Mock
        from lastmile_dataflow.planning.trajectory_validation import TrajectoryStateScorer
        model = SimpleNamespace(lower=np.array([-1.]), upper=np.array([1.]), score=Mock(return_value=None))
        validator = SimpleNamespace(model=model, sim=None, set_q=Mock(),
            geometry=Mock(return_value=dict(valid=True, tcp=np.eye(4), clearance=-1e-8)))
        result = TrajectoryStateScorer(validator, np.eye(3), np.eye(4))(np.array([0.]))
        self.assertEqual(result['quality'], 0.)
        self.assertTrue(result['valid'])


if __name__ == '__main__':
    unittest.main()
