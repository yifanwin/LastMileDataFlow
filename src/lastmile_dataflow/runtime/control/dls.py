"""Base-frame TCP increments composed with upstream GPU DLS.

No collision queries, projections or state writes are performed here.
"""
from dataclasses import dataclass

import torch
from mjlab.envs.mdp.actions.differential_ik import DifferentialIKAction, DifferentialIKActionCfg


@dataclass(kw_only=True)
class BaseFrameDLSCfg(DifferentialIKActionCfg):
    base_site: str
    joint_velocity_limits: tuple[float, ...] = (2.175, 2.175, 2.175, 2.175, 2.61, 2.61, 2.61)

    def build(self, env):
        return BaseFrameDLS(self, env)


class BaseFrameDLS(DifferentialIKAction):
    def __init__(self, cfg, env):
        super().__init__(cfg, env)
        if self._num_joints != 7 or self._action_dim != 6:
            raise ValueError('Panda DLS requires seven arm joints and six relative TCP actions')
        self.base_id = env.sim.mj_model.site(cfg.base_site).id
        self.velocity_step = torch.tensor(cfg.joint_velocity_limits, device=self.device) * env.physics_dt
        self.limit_fraction = torch.zeros(self.num_envs, device=self.device)
        self.q_command = torch.zeros(self.num_envs, 7, device=self.device)

    def process_actions(self, actions):
        # Use the measured site rotation, not a nominal yaw from the station grid.
        rotation = self._env.sim.data.site_xmat[:, self.base_id].reshape(-1, 3, 3)
        world = actions.clone()
        world[:, :3] = torch.einsum('bij,bj->bi', rotation, actions[:, :3])
        world[:, 3:] = torch.einsum('bij,bj->bi', rotation, actions[:, 3:])
        super().process_actions(world)
        self._raw_actions[:] = actions
        self.limit_fraction.zero_()

    def apply_actions(self):
        dq = self.compute_dq()
        bounded = dq.clamp(-self.velocity_step, self.velocity_step)
        current = self._entity.data.joint_pos[:, self._joint_ids]
        candidate = current + bounded
        self.q_command[:] = candidate.clamp(self._joint_lower, self._joint_upper)
        limited = ((dq != bounded) | (self.q_command != candidate)).float().mean(-1)
        self.limit_fraction = torch.maximum(self.limit_fraction, limited)
        self._entity.set_joint_position_target(self.q_command, joint_ids=self._joint_ids)
