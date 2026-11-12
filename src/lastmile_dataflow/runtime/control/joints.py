"""Named joint/actuator groups of one robot inside a compiled MuJoCo model."""

from dataclasses import dataclass

import numpy as np

from .action import InvalidAction


@dataclass(frozen=True)
class JointMap:
    groups: dict[str, tuple[str, ...]]
    actuators: dict[str, tuple[str, ...]]
    ids: dict[str, int]
    addresses: dict[str, int]
    dofs: dict[str, int]
    act_ids: dict[str, int]

    @classmethod
    def build(cls, model, namespace, groups, actuators):
        groups = {group: tuple(names) for group, names in groups.items()}
        actuators = {group: tuple(names) for group, names in actuators.items()}
        ids = {name: model.joint(namespace + name).id for names in groups.values() for name in names}
        return cls(
            groups,
            actuators,
            ids,
            {name: int(model.jnt_qposadr[j]) for name, j in ids.items()},
            {name: int(model.jnt_dofadr[j]) for name, j in ids.items()},
            {name: model.actuator(namespace + name).id for names in actuators.values() for name in names},
        )

    def read(self, data, group):
        return np.array([data.qpos[self.addresses[name]] for name in self.groups[group]])

    def write(self, data, group, values):
        for name, value in zip(self.groups[group], values):
            data.qpos[self.addresses[name]] = value

    def group_dofs(self, group):
        return np.array([self.dofs[name] for name in self.groups[group]])

    def actuator_ids(self, group):
        return [self.act_ids[name] for name in self.actuators[group]]

    def check_joint(self, model, name, value, tolerance=1e-8):
        """Finite target inside the declared joint range."""
        joint = self.ids[name]
        if not np.isfinite(value):
            raise InvalidAction("nonfinite joint target")
        if model.jnt_limited[joint]:
            lo, hi = model.jnt_range[joint]
            if not lo - tolerance <= value <= hi + tolerance:
                raise InvalidAction(f"joint limit: {name}={value}, [{lo}, {hi}]")

    def command_interval(self, model, group, index):
        """Joint range intersected with the matching actuator control range."""
        name = self.groups[group][index]
        joint = self.ids[name]
        lo, hi = -np.inf, np.inf
        if model.jnt_limited[joint]:
            lo, hi = map(float, model.jnt_range[joint])
        if index < len(self.actuators[group]):
            actuator = self.act_ids[self.actuators[group][index]]
            if model.actuator_ctrllimited[actuator]:
                alo, ahi = model.actuator_ctrlrange[actuator]
                lo, hi = max(lo, float(alo)), min(hi, float(ahi))
        if lo > hi:
            raise ValueError("inconsistent joint and actuator limits: " + name)
        return lo, hi


def check_cameras(model, namespace, cameras):
    """Required cameras must exist; never degrade silently to fewer views."""
    names = {name: namespace + name for name in cameras}
    for name in names.values():
        model.camera(name)
    return names
