"""Extended manipulability: limit/collision gradients and orthant penalties on real numbers."""
import unittest

import mujoco
import numpy as np

from lastmile_dataflow.planning.manipulability import (
    collision_potential_gradient,
    extended_quality,
    joint_limit_gradient,
    joint_limit_penalties,
)


class JointLimitTests(unittest.TestCase):
    def test_gradient_matches_finite_difference_and_vanishes_mid_range(self):
        lower, upper = np.array([-2.]), np.array([1.])
        potential = lambda q: (upper - lower) ** 2 / (4 * (upper - q) * (q - lower))
        for q in (-1.7, -.5, .3, .9):
            h = 1e-6
            numeric = (potential(np.array([q + h])) - potential(np.array([q - h]))) / (2 * h)
            np.testing.assert_allclose(joint_limit_gradient(np.array([q]), lower, upper), numeric, rtol=1e-5)
        self.assertAlmostEqual(float(joint_limit_gradient(np.array([-.5]), lower, upper)[0]), 0.)

    def test_only_motion_towards_the_nearer_limit_is_penalized(self):
        minus, plus = joint_limit_penalties(np.array([4., -4., 0.]))
        np.testing.assert_allclose(plus, [1 / np.sqrt(5), 1., 1.])
        np.testing.assert_allclose(minus, [1., 1 / np.sqrt(5), 1.])


class QualityTests(unittest.TestCase):
    def test_unpenalized_quality_is_the_inverse_condition_number(self):
        jacobian = np.random.default_rng(0).normal(size=(6, 7))
        sigma = np.linalg.svd(jacobian, compute_uv=False)
        quality, low, high = extended_quality(jacobian, np.ones(7), np.ones(7))
        self.assertAlmostEqual(quality, sigma.min() / sigma.max())
        self.assertAlmostEqual((low, high), (sigma.min(), sigma.max()))

    def test_collision_scales_translation_rows_only_and_strongest_pair_wins(self):
        jacobian = np.hstack((np.eye(6), np.ones((6, 1)) * .1))
        base, *_ = extended_quality(jacobian, np.ones(7), np.ones(7))
        gradient = np.full(7, 99.)  # factor 0.1 on every joint
        towards_x = np.array([1., 0., 0.])
        one, *_ = extended_quality(jacobian, np.ones(7), np.ones(7), [gradient], [towards_x])
        both, *_ = extended_quality(jacobian, np.ones(7), np.ones(7), [gradient, gradient * 0], [towards_x, towards_x])
        self.assertLess(one, base)
        self.assertAlmostEqual(one, both)  # a weaker duplicate pair adds no penalty
        rotation_only = jacobian.copy()
        rotation_only[:3] = 0
        unchanged, *_ = extended_quality(rotation_only, np.ones(7), np.ones(7), [gradient], [towards_x])
        self.assertAlmostEqual(unchanged, extended_quality(rotation_only, np.ones(7), np.ones(7))[0])

    def test_zero_jacobian_scores_zero(self):
        self.assertEqual(extended_quality(np.zeros((6, 7)), np.ones(7), np.ones(7))[0], 0.)


class MeshDistanceTests(unittest.TestCase):
    def test_separated_meshes_do_not_report_false_contact_from_gjk_zero(self):
        from types import SimpleNamespace
        from lastmile_dataflow.scenes.geometry import geom_distance
        # One deterministic real failure of MuJoCo 3.5 native GJK (#3383).
        rng = np.random.default_rng(7)
        for _ in range(16):
            first = rng.normal(size=(24, 3)) * [.35, .35, .05]
            second = rng.normal(size=(24, 3)) * [.1, .1, .1]
            position = rng.uniform(-.5, .5, 3)
            quaternion = rng.normal(size=4)
            quaternion /= np.linalg.norm(quaternion)
        first_text = ' '.join(f'{v:.5f}' for v in first.ravel())
        second_text = ' '.join(f'{v:.5f}' for v in second.ravel())
        position_text = ' '.join(map(str, position))
        quaternion_text = ' '.join(map(str, quaternion))
        model = mujoco.MjModel.from_xml_string(f"""<mujoco><asset>
            <mesh name="a" vertex="{first_text}"/><mesh name="b" vertex="{second_text}"/>
            </asset><worldbody><geom type="mesh" mesh="a" pos="{position_text}" quat="{quaternion_text}"/>
            <geom type="mesh" mesh="b"/></worldbody></mujoco>""")
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        witnesses = np.zeros(6)
        distance = geom_distance(SimpleNamespace(model=model, data=data), 0, 1, 2., witnesses)
        self.assertAlmostEqual(distance, .0605068229, places=6)
        self.assertAlmostEqual(distance, np.linalg.norm(witnesses[3:]-witnesses[:3]))


class CollisionGradientTests(unittest.TestCase):
    def test_distance_derivative_uses_closest_point_jacobians(self):
        model = mujoco.MjModel.from_xml_string("""<mujoco><worldbody>
          <body name="arm"><joint name="j" type="hinge" axis="0 0 1"/>
            <geom name="tip" type="sphere" size=".05" pos=".5 0 0"/></body>
          <geom name="wall" type="box" size=".05 .5 .5" pos=".8 0 0"/>
        </worldbody></mujoco>""")
        data = mujoco.MjData(model)
        tip, wall = model.geom('tip').id, model.geom('wall').id

        def distance(q):
            data.qpos[0] = q
            mujoco.mj_forward(model, data)
            fromto = np.zeros(6)
            return mujoco.mj_geomDistance(model, data, tip, wall, 1., fromto), fromto

        q = .3
        d, fromto = distance(q)
        jacp = np.zeros((3, 1))
        mujoco.mj_jac(model, data, jacp, None, fromto[:3], model.body('arm').id)
        vector = fromto[3:] - fromto[:3]
        alpha, beta = 20., 1.
        gradient = collision_potential_gradient(d, vector / d, -jacp, alpha, beta)
        potential = lambda value: np.exp(-alpha * value) * value ** -beta
        h = 1e-6
        numeric = (potential(distance(q + h)[0]) - potential(distance(q - h)[0])) / (2 * h)
        np.testing.assert_allclose(gradient, [numeric], rtol=1e-3)


if __name__ == '__main__':
    unittest.main()
