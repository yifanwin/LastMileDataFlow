"""Semantic position targets; physical joints and dataset layouts stay separate."""
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass
class RobotCommand:
    base_xyyaw: object = None
    lift: float | None = None
    arm_q: dict = field(default_factory=dict)
    gripper_width: dict = field(default_factory=dict)


class RobotInterface(Protocol):
    arms: tuple
    camera_names: dict

    def state(self): ...
    def hold(self): ...
    def base_pose(self): ...
    def arm_positions(self, arm): ...
    def gripper_width(self, arm): ...
    def tcp_site(self, arm): ...
    def finger_bodies(self, arm): ...
    def apply(self, command, *, fixed_base=False): ...


def tcp_matrix(robot, arm):
    site = robot.tcp_site(arm)
    pose = np.eye(4)
    pose[:3, 3] = robot.data.site_xpos[site]
    pose[:3, :3] = robot.data.site_xmat[site].reshape(3, 3)
    return pose
