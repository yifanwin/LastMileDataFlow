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
        self.max_dq = max(self.joint_velocity_limits) * env.step_dt
        return BaseFrameDLS(self, env)


def limit_joint_command(current, displacement, previous_command, lower, upper, max_step, substeps=1):
    desired = current + displacement
    position_limited = desired.clamp(lower, upper)
    command_delta = position_limited - previous_command
    bounded_delta = command_delta.clamp(-max_step, max_step)
    command = previous_command + bounded_delta
    # Ramping the reference inside a policy step is normal; only a position clamp or
    # travel beyond one whole policy step at the velocity limit counts as limited.
    limited = ((position_limited != desired) | (command_delta.abs() > max_step * substeps)).float().mean(-1)
    return command, limited


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
        current = self._entity.data.joint_pos[:, self._joint_ids]
        command, limited = limit_joint_command(current, dq, self.q_command, self._joint_lower, self._joint_upper,
                                                self.velocity_step, self._env.cfg.decimation)
        self.q_command[:] = command
        self.limit_fraction = torch.maximum(self.limit_fraction, limited)
        self._entity.set_joint_position_target(self.q_command, joint_ids=self._joint_ids)

    def reset(self, env_ids=None):
        super().reset(env_ids)
        ids = slice(None) if env_ids is None else env_ids
        self.q_command[ids] = self._entity.data.joint_pos[ids][:, self._joint_ids]
        self.limit_fraction[ids] = 0
