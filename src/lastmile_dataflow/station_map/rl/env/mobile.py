"""Body-frame base control and the physical extent of the task-local scene."""

from dataclasses import dataclass

import numpy as np
import torch
from mjlab.managers.action_manager import ActionTerm, ActionTermCfg

from .scene import ROBOT_PREFIX


def base_velocity(actions, rotation, translation_m_s, yaw_rad_s):
    """Limit XY vector magnitude, then rotate body-frame velocity into world axes."""
    xy = actions[:, :2] / actions[:, :2].norm(dim=-1, keepdim=True).clamp(min=1)
    world = torch.einsum("bij,bj->bi", rotation[:, :2, :2], xy) * translation_m_s
    return torch.cat((world, actions[:, 2:3] * yaw_rad_s), -1)


@dataclass(kw_only=True)
class MobileBaseCfg(ActionTermCfg):
    translation_m_s: float
    yaw_rad_s: float

    def build(self, env):
        return MobileBase(self, env)


class MobileBase(ActionTerm):
    """Integrate a base velocity reference using the existing position servos."""

    def __init__(self, cfg, env):
        super().__init__(cfg, env)
        self.ids, _ = self._entity.find_joints(
            ["mobilebase0_joint_mobile_" + suffix for suffix in ("forward", "side", "yaw")],
            preserve_order=True,
        )
        self.base = env.sim.mj_model.site(ROBOT_PREFIX + "base_site").id
        self._raw = torch.zeros(self.num_envs, 3, device=self.device)
        self.command = self._entity.data.joint_pos[:, self.ids].clone()
        self.command_velocity = torch.zeros_like(self.command)

    @property
    def action_dim(self):
        return 3

    @property
    def raw_action(self):
        return self._raw

    def process_actions(self, actions):
        self._raw[:] = actions.clamp(-1, 1)

    def apply_actions(self):
        rotation = self._env.sim.data.site_xmat[:, self.base].reshape(-1, 3, 3)
        self.command_velocity[:] = base_velocity(
            self._raw, rotation, self.cfg.translation_m_s, self.cfg.yaw_rad_s,
        )
        self.command += self.command_velocity * self._env.physics_dt
        self._entity.set_joint_position_target(self.command, joint_ids=self.ids)

    def reset(self, env_ids=None):
        ids = slice(None) if env_ids is None else env_ids
        self._raw[ids] = 0
        self.command_velocity[ids] = 0
        self.command[ids] = self._entity.data.joint_pos[ids][:, self.ids]


class LocalBoundary:
    """Cached collision AABBs for robot and target, shared by CPU reset checks and GPU steps."""

    def __init__(self, model, target_body, anchor, radius, device):
        target = model.body(target_body).id
        target_root = model.body_rootid[target]
        ids = [
            g for g in range(model.ngeom)
            if (model.geom_contype[g] or model.geom_conaffinity[g])
            and (model.body(int(model.geom_bodyid[g])).name.startswith(ROBOT_PREFIX)
                 or model.body_rootid[model.geom_bodyid[g]] == target_root)
        ]
        self.ids = torch.tensor(ids, device=device, dtype=torch.long)
        self.aabb = torch.tensor(model.geom_aabb[ids], device=device, dtype=torch.float32)
        self.anchor = torch.tensor(np.asarray(anchor)[:2], device=device, dtype=torch.float32)
        self.radius = radius
        self.device = device

    def outside(self, data):
        position = torch.as_tensor(data.geom_xpos, device=self.device, dtype=torch.float32)
        rotation = torch.as_tensor(data.geom_xmat, device=self.device, dtype=torch.float32)
        # CPU MuJoCo data has no world batch dimension.
        position = position.reshape(-1, position.shape[-2], 3)[:, self.ids]
        rotation = rotation.reshape(position.shape[0], -1, 3, 3)[:, self.ids]
        center = position + torch.einsum("bnij,nj->bni", rotation, self.aabb[:, :3])
        extent = torch.einsum("bnij,nj->bni", rotation.abs(), self.aabb[:, 3:])
        return ((center[:, :, :2] - self.anchor).abs() + extent[:, :, :2] > self.radius).any(-1).any(-1)
