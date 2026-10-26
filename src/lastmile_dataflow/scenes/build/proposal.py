"""Construction intent and edits staged for the existing runtime transaction."""
from abc import ABC, abstractmethod
import copy
from dataclasses import dataclass, field

import mujoco
import numpy as np

from ..geometry import SupportRegion


class SceneEdit(ABC):
    @abstractmethod
    def apply(self, changes):
        """Append a normalized runtime operation, without touching a live sim."""


@dataclass(frozen=True)
class AddObject(SceneEdit):
    placement: dict

    def apply(self, changes):
        changes['placements'].append(copy.deepcopy(self.placement))


@dataclass(frozen=True)
class MoveTargetOnSupport(SceneEdit):
    body: str
    pose: dict
    support: SupportRegion

    def apply(self, changes):
        rotation = np.zeros(9)
        mujoco.mju_quat2Mat(rotation, np.asarray(self.pose['quaternion_wxyz'], float))
        changes['target_moves'].append({
            'body': self.body, 'pose': copy.deepcopy(self.pose), 'kind': 'surface',
            'support_instance': self.support.support, 'support_geom': self.support.geom,
            'support_height_m': self.support.height, 'reference_up': rotation.reshape(3, 3)[:, 2].tolist()})


@dataclass(frozen=True)
class BasePoseEdit:
    stations: list


@dataclass
class BuildProposal:
    proposal_id: str
    proposal_intent: str
    station_hints: list = field(default_factory=list)
    edits: list[SceneEdit] = field(default_factory=list)
    pose_edit: BasePoseEdit | None = None
    metadata: dict = field(default_factory=dict)

    def runtime_changes(self):
        changes = {'placements': [], 'target_moves': []}
        for edit in self.edits:
            edit.apply(changes)
        return changes


@dataclass(frozen=True)
class ProbeResult:
    status: str
    reason: str
    metrics: dict = field(default_factory=dict)
