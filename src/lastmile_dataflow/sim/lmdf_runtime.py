"""LMDF implementation of the runtime interface: wraps a library snapshot ``Simulation`` unchanged."""

import copy

import mujoco
import numpy as np

from .model.geometry import descendants
from .runtime import ActionSpec, RuntimeState, StepResult
from .simulation import Simulation


def contact_bodies(model, data, bodies):
    """Bodies touching ``bodies`` in the current contact list (excluding ``bodies`` themselves)."""
    bodies = set(bodies)
    touching = set()
    for contact in data.contact[: data.ncon]:
        first, second = (int(model.geom_bodyid[g]) for g in (contact.geom1, contact.geom2))
        if (first in bodies) != (second in bodies):
            touching.add(second if first in bodies else first)
    return touching


class LmdfRuntime:
    """Library snapshot under the existing Simulation and robot adapter; reset reopens the snapshot."""

    def __init__(self, scene_path, robot_config, *, target, scene_id=None, width=256, height=256):
        self.scene_path, self.robot_config, self.target, self.scene_id = scene_path, robot_config, target, scene_id
        self.width, self.height = width, height
        self.sim = None
        self.reset()
        layout = self.sim.robot.record_layout()
        fields = layout.get("action_fields")
        parts = tuple((name, 1) for name in fields) if fields else ((layout["action_layout"], layout["action_size"]),)
        self.action_spec = ActionSpec(
            runtime="lmdf",
            dim=sum(size for _, size in parts),
            parts=parts,
            semantics={"layout": layout["action_layout"], "convention": layout.get("action_convention", "")},
        )

    def reset(self):
        if self.sim is not None:
            self.sim.close()
        self.sim = Simulation.from_snapshot(
            self.scene_path, self.robot_config, target=self.target, scene_id=self.scene_id
        )
        return self.get_state()

    def step(self, action):
        if not self.sim.started:
            self.sim.begin()
        command, stop = self.sim.step(action)
        return StepResult(float(self.sim.data.time), {"robot": self.sim.robot.state()}, {"command": command, "stop": stop})

    def get_state(self):
        sim = self.sim
        target = descendants(sim.model, sim.target_id)
        robot = {b for b in range(sim.model.nbody) if sim.model.body(b).name.startswith(sim.robot.config.namespace)}
        touching = contact_bodies(sim.model, sim.data, target)
        fingers = sim.robot.finger_bodies(sim.robot.arms[0])
        grasped = all(touching & set(group) for group in fingers)
        support = sorted(sim.model.body(b).name for b in touching - robot)
        return RuntimeState(
            time_s=float(sim.data.time),
            robot=sim.robot.state(),
            target=sim.model.body(sim.target_id).name,
            target_pose=np.r_[sim.data.xpos[sim.target_id], sim.data.xquat[sim.target_id]],
            grasped_objects=(sim.model.body(sim.target_id).name,) if grasped else (),
            target_supported=bool(support),
            support_contacts=tuple(support),
        )

    def set_station(self, x, y, yaw):
        """Reset, then place the base at a world pose with the configured initial arm/lift and targets."""
        self.reset()
        initial = copy.deepcopy(self.sim.robot.config.initial)
        initial[self.sim.robot.initial_base_field] = [float(x), float(y), float(yaw)]
        self.sim.robot.initialize(initial)
        mujoco.mj_forward(self.sim.model, self.sim.data)
        return self.get_state()

    def render(self, camera):
        return self.sim.render(self.width, self.height)[camera]

    def close(self):
        self.sim.close()
