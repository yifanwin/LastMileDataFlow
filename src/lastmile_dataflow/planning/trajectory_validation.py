"""Model trajectory checks, independent of native optimizer success.

Geometry is checked at explicit time/joint/geometry resolutions. This is a
discrete protocol, not a continuous collision certificate or a physical trial.
"""
from dataclasses import dataclass

import mujoco
import numpy as np

from ..scenes.geometry import descendants, tcp_pose, geom_distance
from .curobo.planner import pose_error


@dataclass
class TrajectoryValidation:
    valid: bool
    reason: str
    failure_time: float | None = None
    approach_time: float | None = None
    approach_length: float = 0.
    trace: dict | None = None
    checked_states: int = 0


class TrajectoryStateScorer:
    """Exact configuration reuse within one bound scene/yaw/grasp only."""
    def __init__(self, validator, rotation, goal, quality_cache=None):
        self.validator, self.rotation, self.goal = validator, rotation, goal
        self.cache = {}
        self.quality_cache = {} if quality_cache is None else quality_cache

    def __call__(self, q):
        key = tuple(q)
        if key not in self.cache:
            validator, model = self.validator, self.validator.model
            validator.set_q(q)
            hard = validator.geometry(self.goal)
            if key not in self.quality_cache:
                self.quality_cache[key] = model.score(validator.sim, self.rotation)
            score = self.quality_cache[key]
            # A touching soft pair has the zero-quality limit of its collision
            # penalty. Hard validity (including its numerical tolerance) stays
            # independent and is returned for the scoring sample too.
            self.cache[key] = dict(quality=score['quality'] if score is not None else 0.,
                valid=hard['valid'], tcp=hard['tcp'], clearance=hard['clearance'],
                margin=float(np.min(np.minimum(q-model.lower, model.upper-q)/(model.upper-model.lower))))
        return self.cache[key]


class TrajectoryValidator:
    """One worker's fixed model, contact roles and joint limits."""
    def __init__(self, sim, scoring_model, config):
        from ..runtime.outcomes.contacts import ContactSampler
        self.sim, self.model, self.config = sim, scoring_model, config
        m = sim.model
        self.sampler = ContactSampler(sim)
        self.target = descendants(m, sim.target_id)
        ns = sim.robot.config.namespace
        self.pads = {m.geom(ns + name).id for name in config.contact_pad_names}
        self.robot = {b for b in range(m.nbody) if m.body(b).name.startswith(ns)}
        colliding = [g for g in range(m.ngeom) if m.geom_contype[g] or m.geom_conaffinity[g]]
        self.robot_geoms = np.array([g for g in colliding if m.geom_bodyid[g] in self.robot])
        self.environment = np.array([g for g in colliding if m.geom_bodyid[g] not in self.robot])
        self.self_pairs = scoring_model.self_pairs
        self.self_pair_array = np.asarray(self.self_pairs, dtype=int).reshape(-1, 2)
        self.self_pair_set = set(self.self_pairs)
        self.distance_witness = np.zeros(6)
        self.planes = m.geom_type[self.environment] == mujoco.mjtGeom.mjGEOM_PLANE

    def _near_pairs(self):
        """Conservative broad phase over the full hard environment geometry."""
        m, d = self.sim.model, self.sim.data
        centers, radii = d.geom_xpos[self.environment], m.geom_rbound[self.environment]
        a, b = self.self_pair_array.T
        gap = np.linalg.norm(d.geom_xpos[a]-d.geom_xpos[b], axis=1) - m.geom_rbound[a] - m.geom_rbound[b]
        planes = ((m.geom_type[a] == mujoco.mjtGeom.mjGEOM_PLANE)
                  | (m.geom_type[b] == mujoco.mjtGeom.mjGEOM_PLANE))
        yield from self.self_pair_array[(gap < self.config.refine_clearance_m) | planes]
        for g in self.robot_geoms:
            gap = np.linalg.norm(centers-d.geom_xpos[g], axis=1) - radii - m.geom_rbound[g]
            yield from ((int(g), int(o)) for o in self.environment[(gap < self.config.refine_clearance_m) | self.planes])

    def set_q(self, q):
        self.sim.data.qpos[self.model.qpos] = q
        # Only position kinematics, Jacobian data and contacts are needed.
        # Dynamics/constraint solving in mj_forward does not contribute here.
        m, d = self.sim.model, self.sim.data
        mujoco.mj_kinematics(m, d)
        mujoco.mj_comPos(m, d)
        mujoco.mj_collision(m, d)

    def _allowed(self, a, b, terminal):
        m = self.sim.model
        if self.sampler.pair(a, b).floor_base:
            return 'floor'
        target_pad = ((a in self.pads and m.geom_bodyid[b] in self.target)
                      or (b in self.pads and m.geom_bodyid[a] in self.target))
        return 'pad' if terminal and target_pad else None

    def geometry(self, goal=None):
        """Nearest *hard* pair; soft-score exclusions do not apply here."""
        m, d, cfg = self.sim.model, self.sim.data, self.config
        tcp = tcp_pose(self.sim, 'right').copy()
        error = pose_error(tcp, goal) if goal is not None else (np.inf, np.inf)
        terminal = error[0] <= cfg.pose_tolerance_m and error[1] <= cfg.pose_tolerance_rad
        clearance = np.inf
        for a, b in self._near_pairs():
            allowance = self._allowed(a, b, terminal)
            distance = geom_distance(self.sim, a, b, cfg.refine_clearance_m, self.distance_witness)
            tolerance = .005 if allowance == 'floor' else cfg.contact_tolerance_m
            if distance < -tolerance:
                return dict(valid=False, reason='collision', pair=(a, b), tcp=tcp, clearance=float(distance))
            if allowance is None:
                clearance = min(clearance, distance)
        # Native contacts include model-specific self pairs outside the scoring
        # table (e.g. fixed mounts); apply the same strict rule to those too.
        for contact in d.contact[:d.ncon]:
            a, b = int(contact.geom1), int(contact.geom2)
            pair = self.sampler.pair(a, b)
            if not any(pair.robot) or pair.bodies[0] == pair.bodies[1]:
                continue
            if all(pair.robot) and (a, b) not in self.self_pair_set and (b, a) not in self.self_pair_set:
                continue  # robot's explicitly ignored/adjacent link pairs
            allowance = self._allowed(a, b, terminal)
            if contact.dist < -(.005 if allowance == 'floor' else cfg.contact_tolerance_m):
                return dict(valid=False, reason='collision', pair=(a, b), tcp=tcp, clearance=float(contact.dist))
        return dict(valid=True, reason='valid', tcp=tcp, clearance=float(clearance))

    def initial_valid(self, q):
        if np.any(q < self.model.lower) or np.any(q > self.model.upper):
            return False
        self.set_q(q)
        return self.geometry()['valid']

    def terminal_valid(self, q, goal):
        if np.any(q < self.model.lower) or np.any(q > self.model.upper):
            return False
        self.set_q(q)
        error = pose_error(tcp_pose(self.sim, 'right'), goal)
        return (error[0] <= self.config.pose_tolerance_m and error[1] <= self.config.pose_tolerance_rad
                and self.geometry(goal)['valid'])

    def _kinematic_failure(self, curve, q0, velocity_limits, acceleration_limits):
        if not np.allclose(curve.at(curve.time[0]), q0, atol=1e-5, rtol=0):
            return 'start_mismatch'
        for derivative, lower, upper, reason in (
            (0, self.model.lower, self.model.upper, 'joint_limit'),
            (1, -velocity_limits, velocity_limits, 'velocity_limit'),
            (2, -acceleration_limits, acceleration_limits, 'acceleration_limit'),
        ):
            values = curve.at(curve.extrema_times(derivative), derivative)
            if np.any(values < lower - 1e-6) or np.any(values > upper + 1e-6):
                return reason
        return None

    def _state(self, curve, t, goal):
        q = curve.at(t)
        self.set_q(q)
        result = self.geometry(goal)
        m, d = self.sim.model, self.sim.data
        return dict(result, time=float(t), q=q,
                    margin=float(np.min(np.minimum(q - self.model.lower, self.model.upper - q)
                                        / (self.model.upper - self.model.lower))),
                    centers=d.geom_xpos[self.robot_geoms].copy(),
                    rotations=d.geom_xmat[self.robot_geoms].reshape(-1, 3, 3).copy(),
                    radii=m.geom_rbound[self.robot_geoms])

    def _refine(self, curve, left, right, goal):
        """Evaluate midpoints as well, so equal endpoints cannot hide an excursion."""
        middle = self._state(curve, (left['time'] + right['time']) / 2, goal)
        out = []
        for a, b in ((left, middle), (middle, right)):
            angle = np.arccos(np.clip((np.einsum('nij,nij->n', a['rotations'], b['rotations']) - 1) / 2, -1, 1))
            motion = np.linalg.norm(a['centers'] - b['centers'], axis=1) + angle * a['radii']
            near = min(a['clearance'], b['clearance']) < self.config.refine_clearance_m
            approach = pose_error(b['tcp'], goal)[0] < self.config.approach_distance_m
            if (near or approach) and motion.max(initial=0) > self.config.validation_geometry_step_m:
                out.extend(self._refine(curve, a, b, goal))
            else:
                out.append(a)
        return out

    def validate(self, curve, q0, goal, velocity_limits, acceleration_limits):
        cfg = self.config
        reason = self._kinematic_failure(curve, q0, velocity_limits, acceleration_limits)
        if reason:
            return TrajectoryValidation(False, reason)
        vmax = np.max(np.abs(curve.at(curve.extrema_times(1), 1)))
        step = min(cfg.validation_dt_s, cfg.validation_joint_step_rad / max(vmax, 1e-12))
        times = np.unique(np.r_[np.linspace(curve.time[0], curve.time[-1],
                                           max(2, int(np.ceil(np.ptp(curve.time) / step)) + 1)), curve.time])
        left = self._state(curve, times[0], goal)
        if not left['valid']:
            return TrajectoryValidation(False, left['reason'], left['time'], checked_states=1)
        dense = []
        for t in times[1:]:
            right = self._state(curve, t, goal)
            interval = self._refine(curve, left, right, goal)
            for item in [*interval, right]:
                if not item['valid']:
                    return TrajectoryValidation(False, item['reason'], item['time'],
                                                checked_states=len(dense)+len(interval)+1)
            dense.extend(interval)
            left = right
        dense.append(left)
        tcp = np.array([item['tcp'] for item in dense])
        error = pose_error(tcp[-1], goal)
        if error[0] > cfg.pose_tolerance_m or error[1] > cfg.pose_tolerance_rad:
            return TrajectoryValidation(False, 'terminal_pose', dense[-1]['time'], checked_states=len(dense))
        start, advance = approach_segment(tcp, goal, cfg)
        if advance < cfg.minimum_approach_m:
            return TrajectoryValidation(False, 'approach', dense[start]['time'], checked_states=len(dense))
        q = np.array([item['q'] for item in dense])
        s = np.r_[0., np.cumsum(np.linalg.norm(np.diff(q, axis=0) / (self.model.upper - self.model.lower), axis=1))]
        trace = dict(time=np.array([item['time'] for item in dense]), s=s, q=q, tcp=tcp,
                     clearance=np.array([item['clearance'] for item in dense]),
                     margin=np.array([item['margin'] for item in dense]))
        return TrajectoryValidation(True, 'valid', approach_time=dense[start]['time'],
                                    approach_length=advance, trace=trace, checked_states=len(dense))


def approach_segment(tcp, goal, config):
    """Identify the final continuous approach corridor, from the end backwards."""
    axis = goal[:3, 2]
    delta = goal[:3, 3] - tcp[:, :3, 3]
    depth = delta @ axis
    lateral = np.linalg.norm(delta - depth[:, None] * axis, axis=1)
    rotation = np.array([pose_error(pose, goal)[1] for pose in tcp])
    start = len(tcp) - 1
    for i in range(len(tcp) - 2, -1, -1):
        if (lateral[i] > config.approach_lateral_m or rotation[i] > config.approach_rotation_rad
                or depth[i] < -config.pose_tolerance_m
                or depth[i] < np.max(depth[i + 1:]) - config.approach_backtrack_m):
            break
        start = i
    return start, float(depth[start] - depth[-1])
