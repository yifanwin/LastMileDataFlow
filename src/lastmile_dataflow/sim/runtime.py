"""Small runtime interface shared by the LMDF Simulation and the RoboCasa native Environment.

One call style, two runtimes: the action layout, units and control semantics differ and are
described by ``action_spec``; numbers are never assumed interchangeable between runtimes.
"""

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class ActionSpec:
    """Action layout of one runtime: ordered (part, size) slices and what each part commands."""

    runtime: str
    dim: int
    parts: tuple  # ((name, size), ...) in vector order
    semantics: dict  # name -> control meaning (controller type, frame, units, delta/absolute)

    def __post_init__(self):
        if sum(size for _, size in self.parts) != self.dim:
            raise ValueError("action parts do not cover the action dimension")


@dataclass(frozen=True)
class StepResult:
    """One control period: time after the step and the runtime's own end-of-episode information."""

    time_s: float
    observation: dict
    native: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeState:
    """Measured robot and target facts at one instant (not a resumable checkpoint)."""

    time_s: float
    robot: dict  # base_xyyaw (world) plus the runtime's own joint readout
    target: str
    target_pose: np.ndarray  # body origin xyz + quaternion wxyz, world
    grasped_objects: tuple  # objects passing the runtime's own two-finger grasp check
    target_supported: bool  # target touches any environment geom (not robot, not itself)
    support_contacts: tuple = ()


class SceneRuntime(Protocol):
    action_spec: ActionSpec

    def reset(self) -> RuntimeState: ...

    def step(self, action) -> StepResult: ...

    def get_state(self) -> RuntimeState: ...

    def set_station(self, x: float, y: float, yaw: float) -> RuntimeState: ...

    def render(self, camera: str) -> np.ndarray: ...

    def close(self) -> None: ...
