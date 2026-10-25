"""Contact telemetry only: never change physics, commands, or task verdicts."""
import mujoco
import numpy as np
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ContactPair:
    bodies: tuple
    body_names: tuple
    geom_ids: tuple
    geom_names: tuple
    robot: tuple
    floor_base: bool
    same_hand: bool


class ContactFrame:
    """One physics boundary's forces, consumed before the next simulation step."""
    def __init__(self, sim, contacts):
        self.sim, self.contacts = sim, contacts

    def observations(self, limit=None):
        result = []
        for index, pair, force, bearing, intended, forbidden in self.contacts[:limit]:
            contact = self.sim.data.contact[index]
            result.append({
                'body_names': list(pair.body_names), 'geom_names': list(pair.geom_names),
                'geom_ids': list(pair.geom_ids), 'distance_m': float(contact.dist),
                'position_world': contact.pos.tolist(), 'normal_force_n': force,
                'time_s': float(self.sim.data.time), 'robot_contact': any(pair.robot),
                'allowed_floor_base': pair.floor_base, 'intended_target_contact': intended,
                'forbidden': forbidden, 'kind': 'self_collision' if all(pair.robot) else
                'environment_collision' if any(pair.robot) else 'object_contact'})
        return result


class ContactSampler:
    """Cache fixed names/classifications and read each contact force once per tick."""
    def __init__(self, sim):
        self.sim = sim
        self.pairs = {}
        self.force = np.zeros(6)

    def pair(self, g1, g2):
        key = (g1, g2)
        if key not in self.pairs:
            model = self.sim.model
            namespace = self.sim.robot.config.namespace if self.sim.robot else 'robot_0/'
            bodies = tuple(int(model.geom_bodyid[g]) for g in key)
            names = tuple(model.body(b).name or '' for b in bodies)
            geometry = tuple(model.geom(g).name or '' for g in key)
            robot = tuple(name.startswith(namespace) for name in names)
            floor_base = any('floor' in g.lower() for g in geometry) and any(
                r and ('base' in n or 'wheel' in n) for r, n in zip(robot, names))
            same_hand = any(all(n.startswith(namespace+'ee_finger_'+side) for n in names)
                            for side in ('l', 'r'))
            self.pairs[key] = ContactPair(bodies, names, key, geometry, robot, floor_base, same_hand)
        return self.pairs[key]

    def capture(self, *, target_bodies=(), fingers=()):
        result = []
        for index, contact in enumerate(self.sim.data.contact):
            pair = self.pair(int(contact.geom1), int(contact.geom2))
            a, b = pair.bodies
            if a == b:
                continue
            mujoco.mj_contactForce(self.sim.model, self.sim.data, index, self.force)
            force = float(self.force[0])
            intended = a in fingers and b in target_bodies or b in fingers and a in target_bodies
            forbidden = any(pair.robot) and not (pair.floor_base or intended or pair.same_hand)
            result.append((index, pair, force, contact.efc_address >= 0, intended, forbidden))
        return ContactFrame(self.sim, result)


def contact_observations(sim, *, target_bodies=(), fingers=()):
    """All named contacts. `forbidden` is a quality flag, NOT physical illegality."""
    return ContactSampler(sim).capture(target_bodies=target_bodies, fingers=fingers).observations()


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

    def update_frame(self, frame):
        self.contact_count += len(frame.contacts)
        self.forbidden_contact_count += len({tuple(sorted(pair.body_names))
            for _, pair, _, _, _, forbidden in frame.contacts if forbidden})
        remaining = 32 - len(self.examples)
        if remaining > 0:
            self.examples.extend(frame.observations(remaining))

    def metrics(self, *, task_success=None, simulation_valid=None):
        return {'task_success': task_success, 'simulation_valid': simulation_valid,
                'forbidden_contact_count': self.forbidden_contact_count,
                'contact_count': self.contact_count, 'contact_policy': CONTACT_POLICY,
                'forbidden_contact_count_unit': 'unordered_body_pair_per_physics_tick'}
