"""Extended manipulability of a fixed-base arm configuration (Vahrenkamp 2012, II).

Joint limits and collision-distance gradients scale the TCP Jacobian elementwise
per motion-direction orthant; the score is min/max singular value over all 64
orthants. Collision scaling acts on the three translation rows only, as in the
paper: rotation rows get no direct collision penalty.
"""
from itertools import product

import mujoco
import numpy as np

from ..scenes.geometry import descendants

ORTHANTS = np.array(list(product((-1., 1.), repeat=6)))  # (64, 6)


def joint_limit_gradient(q, lower, upper):
    """d/dq of h = (b-a)^2 / (4 (b-q)(q-a)); zero at mid-range, +-inf at the limits."""
    span = upper - lower
    return span ** 2 * (2 * q - lower - upper) / (4 * (upper - q) ** 2 * (q - lower) ** 2)


def joint_limit_penalties(gradient):
    """p^- and p^+ per joint: shrink only the direction moving towards the nearer limit."""
    minus = 1 / np.sqrt(1 + np.maximum(0., -gradient))
    plus = 1 / np.sqrt(1 + np.maximum(0., gradient))
    return minus, plus


def collision_potential_gradient(distance, direction, jacobian_difference, alpha, beta):
    """grad_q of P = exp(-alpha d) d^-beta, with d'(q) = n^T (J_o - J_m)."""
    slope = -np.exp(-alpha * distance) * distance ** -beta * (alpha + beta / distance)
    return slope * (direction @ jacobian_difference)


def extended_quality(jacobian, minus, plus, collision_gradients=(), collision_vectors=()):
    """Inverse condition number of the penalized Jacobian over all orthants.

    `jacobian` is the scaled 6x7 TCP Jacobian in base axes; `collision_vectors`
    are v = p_other - p_robot in the same axes. Several pairs combine by the
    strongest elementwise penalty, so splitting one obstacle into more geoms
    does not accumulate penalty. Returns (quality, sigma_min, sigma_max).
    """
    signs = ORTHANTS[:, :, None] * jacobian[None]
    limits = np.where(signs < 0, minus, np.where(signs > 0, plus, 1.))
    collision = np.ones_like(limits)
    if len(collision_gradients):
        factors = 1 / np.sqrt(1 + np.abs(np.asarray(collision_gradients)))  # (C, 7)
        towards = ORTHANTS[:, :3, None] * np.asarray(collision_vectors).T[None] > 0  # (64, 3, C)
        collision[:, :3] = np.where(towards[..., None], factors[None, None], 1.).min(axis=2)
    sigma = np.linalg.svd(limits * collision * jacobian[None], compute_uv=False)
    low, high = float(sigma.min()), float(sigma.max())
    return (low / high if high > 0 else 0.), low, high


class ScoringModel:
    """Fixed MuJoCo geometry roles for scoring arm configurations of one robot.

    Moving geoms are the arm and gripper below the first arm link. Environment
    pairs exclude the target subtree (hard-checked only), planes, and, unless
    enabled, the bodies supporting the target. Self pairs follow the planner's
    self-collision ignore table, so scoring and IK share one notion of adjacency.
    """
    def __init__(self, sim, side, config, *, link_bodies, ignore, support_bodies=()):
        m = sim.model
        self.config = config
        names = [f'{sim.robot.config.namespace}robot0_joint{i}' for i in range(1, 8)]
        joints = [m.joint(name).id for name in names]
        self.dofs = np.array([m.jnt_dofadr[j] for j in joints])
        self.qpos = np.array([m.jnt_qposadr[j] for j in joints])
        self.lower, self.upper = m.jnt_range[joints, 0].copy(), m.jnt_range[joints, 1].copy()
        self.site = sim.robot.tcp_site(side)
        body_link = {m.body(name).id: link for link, bodies in link_bodies.items() for name in bodies}
        root = min(m.body(name).id for name in link_bodies['panda_link1'])
        robot = {b for b in range(m.nbody) if m.body(b).name.startswith(sim.robot.config.namespace)}
        moving = descendants(m, root)
        colliding = [g for g in range(m.ngeom) if m.geom_contype[g] or m.geom_conaffinity[g]]
        self.moving = np.array([g for g in colliding if m.geom_bodyid[g] in moving])
        excluded = descendants(m, sim.target_id) | (set() if config.penalize_support_surface else set(support_bodies))
        self.environment = np.array([g for g in colliding if m.geom_bodyid[g] not in robot
                                     and m.geom_bodyid[g] not in excluded and m.geom_type[g] != mujoco.mjtGeom.mjGEOM_PLANE])
        pairs = set()
        for a in self.moving:
            for b in colliding:
                ba, bb = int(m.geom_bodyid[a]), int(m.geom_bodyid[b])
                if bb not in robot or ba == bb or bb not in body_link or ba not in body_link:
                    continue
                la, lb = body_link[ba], body_link[bb]
                if la == lb or lb in ignore.get(la, ()) or la in ignore.get(lb, ()):
                    continue
                # Fixed roles: the robot-side point belongs to the deeper body.
                pairs.add((a, b) if ba > bb else (b, a))
        self.self_pairs = sorted(pairs)
        self.support_bodies = sorted(support_bodies)

    def jacobian(self, sim, rotation):
        m, d = sim.model, sim.data
        jacp, jacr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
        mujoco.mj_jacSite(m, d, jacp, jacr, self.site)
        jacobian = np.vstack((rotation.T @ jacp[:, self.dofs], rotation.T @ jacr[:, self.dofs]))
        jacobian[:3] /= self.config.characteristic_length_m
        return jacobian

    def _point_jacobian(self, sim, point, body):
        jacp = np.zeros((3, sim.model.nv))
        mujoco.mj_jac(sim.model, sim.data, jacp, None, point, body)
        return jacp[:, self.dofs]

    def _environment_pairs(self, sim):
        d, m = sim.data, sim.model
        reach = self.config.collision_query_m
        centers, radii = d.geom_xpos[self.environment], m.geom_rbound[self.environment]
        for g in self.moving:
            gap = np.linalg.norm(centers - d.geom_xpos[g], axis=1) - radii - m.geom_rbound[g]
            yield from ((int(g), int(o)) for o in self.environment[gap < reach])

    def score(self, sim, rotation):
        """Score the current MuJoCo state; None when a penalized pair touches."""
        m, d, config = sim.model, sim.data, self.config
        q = d.qpos[self.qpos]
        minus, plus = joint_limit_penalties(joint_limit_gradient(q, self.lower, self.upper))
        gradients, vectors, nearest = [], [], np.inf
        fromto = np.zeros(6)
        for robot_geom, other in [*self._environment_pairs(sim), *self.self_pairs]:
            distance = mujoco.mj_geomDistance(m, d, robot_geom, other, config.collision_query_m, fromto)
            if distance >= config.collision_query_m:
                continue
            if distance <= 0:
                return None
            nearest = min(nearest, distance)
            point_m, point_o = fromto[:3].copy(), fromto[3:].copy()
            vector = point_o - point_m
            difference = (self._point_jacobian(sim, point_o, int(m.geom_bodyid[other]))
                          - self._point_jacobian(sim, point_m, int(m.geom_bodyid[robot_geom])))
            gradients.append(collision_potential_gradient(
                distance, vector / distance, difference, config.collision_alpha, config.collision_beta))
            vectors.append(rotation.T @ vector)
        quality, low, high = extended_quality(self.jacobian(sim, rotation), minus, plus, gradients, vectors)
        return {'quality': quality, 'sigma_min': low, 'sigma_max': high,
                'penalized_pairs': len(gradients), 'nearest_m': float(nearest)}

