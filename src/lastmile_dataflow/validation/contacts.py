"""Contact telemetry only: never change physics, commands, or task verdicts."""
import mujoco
import numpy as np

CONTACT_POLICY = 'runtime-record-only-v1'
SIMULATION_FAILURES = frozenset(('nonfinite_physics', 'nonfinite_physics_state',
                                 'nonfinite_state', 'mujoco_warning'))


def simulation_failure(sim):
    d = sim.data
    if not all(np.isfinite(x).all() for x in (d.qpos, d.qvel, d.qacc, d.ctrl)):
        return 'nonfinite_physics'
    # MuJoCo may reset an unstable state before we can see its NaN/huge values.
    # Its warning counters must therefore also be checked, not just finiteness.
    if any(w.number for w in d.warning):
        return 'mujoco_warning'
    return None


def contact_observations(sim, *, target_bodies=(), fingers=()):
    """All named contacts. `forbidden` is a quality flag, NOT physical illegality."""
    m, d = sim.model, sim.data
    namespace = sim.robot.config.namespace if sim.robot else 'robot_0/'
    result = []
    for i, c in enumerate(d.contact):
        geoms = [int(c.geom1), int(c.geom2)]
        bodies = [int(m.geom_bodyid[g]) for g in geoms]
        if bodies[0] == bodies[1]:
            continue
        names = [m.body(b).name or '' for b in bodies]
        geometry = [m.geom(g).name or '' for g in geoms]
        robot = [n.startswith(namespace) for n in names]
        floor_base = any('floor' in g.lower() for g in geometry) and any(
            r and ('base' in n or 'wheel' in n) for r, n in zip(robot, names))
        intended = (bodies[0] in fingers and bodies[1] in target_bodies or
                    bodies[1] in fingers and bodies[0] in target_bodies)
        same_hand = any(all(n.startswith(namespace+'ee_finger_'+side) for n in names)
                        for side in ('l', 'r'))
        force = np.zeros(6)
        mujoco.mj_contactForce(m, d, i, force)
        result.append({'body_names': names, 'geom_names': geometry, 'geom_ids':geoms,
                       'distance_m': float(c.dist), 'position_world': c.pos.tolist(),
                       'normal_force_n': float(force[0]), 'time_s': float(d.time),
                       'robot_contact': any(robot), 'allowed_floor_base': floor_base,
                       'intended_target_contact': intended,
                       'forbidden': bool(any(robot) and not (floor_base or intended or same_hand)),
                       'kind': 'self_collision' if all(robot) else
                               'environment_collision' if any(robot) else 'object_contact'})
    return result


class ContactStatistics:
    """Count body-pair/tick observations, not mesh manifolds or collision episodes."""
    def __init__(self):
        self.forbidden_contact_count = 0
        self.contact_count = 0
        self.examples = []

    def update(self, contacts):
        self.contact_count += len(contacts)
        self.forbidden_contact_count += len({tuple(sorted(c['body_names'])) for c in contacts
                                             if c.get('forbidden', False)})
        self.examples.extend(contacts[:max(0, 32-len(self.examples))])

    def metrics(self, *, task_success=None, simulation_valid=None):
        return {'task_success': task_success, 'simulation_valid': simulation_valid,
                'forbidden_contact_count': self.forbidden_contact_count,
                'contact_count': self.contact_count, 'contact_policy': CONTACT_POLICY,
                'forbidden_contact_count_unit': 'unordered_body_pair_per_physics_tick'}
