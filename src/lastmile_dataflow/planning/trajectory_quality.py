"""Deterministic candidate geometry and arc-length bottleneck scoring."""
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import BPoly, PPoly


def joint_distance(a, b, span):
    return np.linalg.norm((np.asarray(a) - b) / span, axis=-1)


def representatives(q, span, threshold=.05, count=4):
    """Farthest-first coverage; stable input/seed order resolves ties."""
    q = np.asarray(q)
    if len(q) == 0:
        return []
    chosen = [0]
    nearest = joint_distance(q, q[0], span)
    while len(chosen) < min(count, len(q)):
        index = int(np.argmax(nearest))
        if nearest[index] <= threshold:
            break
        chosen.append(index)
        nearest = np.minimum(nearest, joint_distance(q, q[index], span))
    return chosen


def terminal_clusters(q, span, threshold=.05):
    """Stable diameter-bounded regions, rather than transitive/chain clusters."""
    clusters, labels = [], []
    for i, item in enumerate(q):
        label = next((k for k, members in enumerate(clusters)
                      if np.all(joint_distance(np.asarray(q)[members], item, span) <= threshold)), len(clusters))
        if label == len(clusters):
            clusters.append([])
        clusters[label].append(i)
        labels.append(label)
    return np.array(labels, dtype=int), clusters


@dataclass
class TrajectoryCandidate:
    candidate_id: int
    representative_id: int
    q_seed: np.ndarray
    seed_kind: str
    source_query: int = -1
    source_candidate: int = -1
    submitted: bool = False
    solver_success: bool = False
    curve: object = None


class JointCurve:
    """Piecewise quintic q(t), with analytic derivatives and interval extrema.

    Inputs are *evaluated* positions, velocities and accelerations at native
    B-spline knot boundaries, never the optimizer's action/control points.
    A degree <=5 spline is reproduced exactly on each such interval.
    """
    def __init__(self, time, q, velocity, acceleration):
        self.time = np.asarray(time, float)
        self.q = np.asarray(q, float)
        if len(self.time) < 2 or np.any(np.diff(self.time) <= 0):
            raise ValueError('trajectory needs increasing finite times')
        if not all(np.isfinite(v).all() for v in (self.time, self.q, velocity, acceleration)):
            raise ValueError('nonfinite trajectory')
        self.polynomial = PPoly.from_bernstein_basis(BPoly.from_derivatives(
            self.time, [[p, v, a] for p, v, a in zip(self.q, velocity, acceleration)]))

    def at(self, time, derivative=0):
        return self.polynomial(time, nu=derivative)

    def extrema_times(self, derivative=0):
        """All per-joint extrema, including interval boundaries."""
        roots = []
        c = self.polynomial.derivative(derivative + 1).c
        for i, width in enumerate(np.diff(self.time)):
            for j in range(self.q.shape[1]):
                r = np.roots(np.trim_zeros(c[:, i, j], 'f'))
                roots.extend(self.time[i] + float(v.real) for v in r
                             if abs(v.imag) < 1e-9 and 0 < v.real < width)
        return np.unique(np.r_[self.time, roots])


def trajectory_seeds(q0, q_seed, horizon, lower, upper, rng, perturbation, history=None):
    """One linear seed plus one endpoint-corrected history or perturbed seed."""
    u = np.linspace(0, 1, horizon)
    linear = q0 + u[:, None] * (q_seed - q0)
    if history is not None:
        old = np.asarray(history)
        second = np.array([np.interp(u, np.linspace(0, 1, len(old)), old[:, j]) for j in range(len(q0))]).T
        smooth = u * u * (3 - 2 * u)
        second += (1 - smooth[:, None]) * (q0 - second[0]) + smooth[:, None] * (q_seed - second[-1])
    else:
        anchors = np.vstack((np.zeros_like(q0), rng.normal(size=(2, len(q0))) * (upper - lower) * perturbation,
                             np.zeros_like(q0)))
        second = linear + np.array([np.interp(u, [0, 1/3, 2/3, 1], anchors[:, j]) for j in range(len(q0))]).T
    second = np.clip(second, lower, upper)
    second[0], second[-1] = q0, q_seed
    return linear, second


def planning_rate(attempted, completed, valid, *, incomplete=False):
    return valid / attempted if attempted and completed == attempted and not incomplete else None


@dataclass
class TrajectoryQuality:
    full: float
    post: float | None
    length: float
    samples: list
    rounds: list
    full_bottleneck: dict
    post_bottleneck: dict | None
    stop_reason: str


def _time_at_s(curve, s, trace):
    t = float(np.interp(s, trace['s'], trace['time']))
    if s == 0:
        t = float(curve.time[0])
    elif s == trace['s'][-1]:
        t = float(curve.time[-1])
    return t


def _sample_record(curve, s, trace, evaluate, config, approach_time):
    t = _time_at_s(curve, s, trace)
    q = curve.at(t)
    item = evaluate(q)
    u = s / trace['s'][-1] if trace['s'][-1] > 0 else 0.
    approach = t >= approach_time
    transition = u < config.transition_fraction
    return dict(item, q=q, time=t, s=float(s), u=float(u), initial_transition=bool(transition),
                approach=bool(approach), is_terminal=bool(t >= curve.time[-1] - 1e-9),
                phase='approach' if approach else 'initial_transition' if transition else 'transit')


def _priority(left, right, trace, config):
    inside = (trace['s'] >= left['s']) & (trace['s'] <= right['s'])
    tcp = trace['tcp'][inside]
    # Include interval endpoints; internal travel (not net displacement) matters.
    positions = np.array([left['tcp'], *tcp, right['tcp']])
    translation = np.linalg.norm(np.diff(positions[:, :3, 3], axis=0), axis=1).sum()
    rotation = sum(np.arccos(np.clip((np.trace(a[:3, :3].T @ b[:3, :3]) - 1) / 2, -1, 1))
                   for a, b in zip(positions[:-1], positions[1:]))
    clearance = min(left['clearance'], right['clearance'], np.min(trace['clearance'][inside], initial=np.inf))
    margin = min(left['margin'], right['margin'], np.min(trace['margin'][inside], initial=np.inf))
    change = abs(left['quality'] - right['quality']) / max(abs(left['quality']), abs(right['quality']), 1e-6)
    return max(change / config.refine_quality, config.refine_clearance_m / max(clearance, 1e-12),
               config.refine_limit_margin / max(margin, 1e-12),
               (right['s'] - left['s']) / config.refine_joint_length,
               translation / config.refine_tcp_m, rotation / config.refine_tcp_rad)


def score_trajectory(curve, trace, evaluate, config, approach_time):
    """Nested 16→32 samples of this curve, with shared full/post diagnostics."""
    length = float(trace['s'][-1])
    locations = np.linspace(0, length, config.quality_initial_samples) if length > 0 else [0.]
    if length > 0:
        locations = np.r_[locations, length * config.transition_fraction,
                          np.interp(approach_time, trace['time'], trace['s'])]
    samples = [_sample_record(curve, s, trace, evaluate, config, approach_time) for s in np.unique(locations)]
    rounds = []
    while True:
        samples.sort(key=lambda item: item['s'])
        for index, item in enumerate(samples):
            item['sample_index'] = index
        full = min(samples, key=lambda item: (item['quality'], item['s']))
        post_samples = [item for item in samples if item['s'] >= config.transition_fraction * length - 1e-12]
        post = min(post_samples, key=lambda item: (item['quality'], item['s'])) if length else None
        rounds.append(dict(n=len(samples), full=full['quality'], post=post['quality'] if post else None,
                           s=full['s']))
        if len(samples) >= config.quality_max_samples or length == 0:
            break
        intervals = [(_priority(a, b, trace, config), b['s'] - a['s'], a, b)
                     for a, b in zip(samples[:-1], samples[1:])]
        intervals.sort(key=lambda item: (item[0] > 1, item[0] if item[0] > 1 else item[1],
                                         -item[2]['s']), reverse=True)
        for _, _, a, b in intervals:
            middle_s = (a['s'] + b['s']) / 2
            q = curve.at(_time_at_s(curve, middle_s, trace))
            if not any(np.array_equal(q, old['q']) for old in samples):
                samples.append(_sample_record(curve, middle_s, trace, evaluate, config, approach_time))
                break
        else:
            break
    return TrajectoryQuality(float(full['quality']), float(post['quality']) if post else None, length,
                             samples, rounds, full, post,
                             'budget' if len(samples) >= config.quality_max_samples else 'no_distinct_configuration')


def ranked_candidates(rows, retain=4):
    """All valid candidates must already be scored; no terminal-region quotas."""
    return sorted(rows, key=lambda row: (-row['quality'].full, row['quality'].length,
                                        row['candidate'].candidate_id))[:retain]
