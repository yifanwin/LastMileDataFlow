"""Configure mjlab managers; stepping remains entirely upstream."""
from dataclasses import dataclass

import torch
from mjlab.actuator import XmlActuatorCfg
from mjlab.entity import EntityCfg, EntityArticulationInfoCfg
from mjlab.envs import ManagerBasedRlEnv, ManagerBasedRlEnvCfg
from mjlab.managers.action_manager import ActionTerm, ActionTermCfg
from mjlab.managers.event_manager import EventTermCfg
from mjlab.managers.metrics_manager import MetricsTermCfg
from mjlab.managers.observation_manager import ObservationGroupCfg, ObservationTermCfg
from mjlab.managers.reward_manager import RewardTermCfg
from mjlab.managers.termination_manager import TerminationTermCfg
from mjlab.scene import SceneCfg
from mjlab.sensor import ContactMatch, ContactSensorCfg
from mjlab.sim import MujocoCfg, SimulationCfg

from ...scenes.loading.rl_scene import RLSceneConfig, prepare_rl_scene
from ..control.dls import BaseFrameDLSCfg
from . import terms


ROBOT_PREFIX = 'robot/robot_0/'


@dataclass(kw_only=True)
class GripperCfg(ActionTermCfg):
    def build(self, env):
        return Gripper(self, env)


class Gripper(ActionTerm):
    """One normalized width controls the two signed Panda finger slides."""
    def __init__(self, cfg, env):
        super().__init__(cfg, env)
        self.ids, _ = self._entity.find_joints(
            ['gripper0_right_finger_joint1', 'gripper0_right_finger_joint2'], preserve_order=True)
        self._raw = torch.zeros(self.num_envs, 1, device=self.device)
        self.targets = torch.zeros(self.num_envs, 2, device=self.device)

    @property
    def action_dim(self):
        return 1

    @property
    def raw_action(self):
        return self._raw

    def process_actions(self, actions):
        self._raw[:] = actions
        half_width = (actions[:, 0] + 1) * .02
        self.targets[:, 0], self.targets[:, 1] = half_width, -half_width

    def apply_actions(self):
        self._entity.set_joint_position_target(self.targets, joint_ids=self.ids)

    def reset(self, env_ids=None):
        ids = slice(None) if env_ids is None else env_ids
        self._raw[ids] = 0
        self.targets[ids] = torch.tensor([.04, -.04], device=self.device)


def contact_sensor(name, primary, secondary=None):
    return ContactSensorCfg(name=name, primary=primary, secondary=secondary,
                            fields=('found', 'force'), reduce='maxforce')


def build_sensors(prepared):
    target = ContactMatch(mode='subtree', pattern=prepared.target_body)
    robot = ContactMatch(mode='subtree', pattern=ROBOT_PREFIX + 'robot0_base')
    sensors = [contact_sensor('self_collision', robot, robot)]
    for index, finger in enumerate(('left', 'right')):
        sensors.append(contact_sensor(f'finger_{index}', ContactMatch(
            mode='subtree', pattern=ROBOT_PREFIX + f'gripper0_right_{finger}finger'), target))
    for index, body in enumerate(prepared.scene_spec.worldbody.bodies):
        if body.name == prepared.target_body:
            continue
        other = ContactMatch(mode='subtree', pattern=body.name)
        sensors.append(contact_sensor(f'support_{index}', target, other))
        sensors.append(contact_sensor(f'environment_{index}', robot, other))
    for index, geom in enumerate(prepared.scene_spec.worldbody.geoms):
        if not (geom.contype or geom.conaffinity):
            continue
        other = ContactMatch(mode='geom', pattern=geom.name)
        sensors.append(contact_sensor(f'support_world_{index}', target, other))
        # Normal base-floor contact is excluded, while the arm remains monitored.
        primary = (ContactMatch(mode='subtree', pattern=ROBOT_PREFIX + 'mobilebase0_support')
                   if 'floor' in geom.name.lower() else robot)
        sensors.append(contact_sensor(f'environment_world_{index}', primary, other))
    # Nonfinger contacts with the cup are illegal, without penalizing grasping.
    for index, body in enumerate(prepared.robot_spec.bodies):
        if 'finger' not in body.name and body.geoms:
            sensors.append(contact_sensor(f'nonfinger_{index}', ContactMatch(
                mode='body', pattern='robot/' + body.name), target))
    return tuple(sensors)


class StationEnv(ManagerBasedRlEnv):
    def __init__(self, config, *, gpu, seed, num_envs=None):
        self.research_config = config
        self.prepared = prepare_rl_scene(RLSceneConfig(**config['scene']))
        self.task = None
        prepared = self.prepared
        initial = {name.split('/')[-1]: value for name, value in prepared.initial_qpos.items()
                   if name.startswith('robot_0/')}
        root = prepared.robot_spec.bodies[1]
        robot = EntityCfg(spec_fn=prepared.copy_robot,
                          init_state=EntityCfg.InitialStateCfg(pos=tuple(root.pos), rot=tuple(root.quat),
                                                              joint_pos=initial),
                          articulation=EntityArticulationInfoCfg(actuators=(
                              XmlActuatorCfg(target_names_expr=('.*',)),)))
        action = config['action']
        env_cfg = config['environment']
        sensors = build_sensors(prepared)
        cfg = ManagerBasedRlEnvCfg(
            scene=SceneCfg(num_envs=num_envs or env_cfg['num_envs'], env_spacing=0.,
                           entities={'robot': robot}, sensors=sensors,
                           spec_fn=prepared.attach_scene),
            sim=SimulationCfg(mujoco=MujocoCfg(timestep=config['scene']['physics_dt'],
                                               impratio=10., cone='elliptic', gravity=(0., 0., -9.8),
                                               disableflags=() if prepared.multiccd else ('multiccd',))),
            seed=seed, decimation=env_cfg['decimation'], episode_length_s=env_cfg['episode_s'],
            auto_reset=False, is_finite_horizon=True, scale_rewards_by_dt=False,
            actions={
                'tcp': BaseFrameDLSCfg(entity_name='robot', actuator_names=[f'robot0_torq_j{i}' for i in range(1, 8)],
                                      frame_type='site', frame_name='gripper0_right_grip_site',
                                      base_site=ROBOT_PREFIX + 'base_site',
                                      delta_pos_scale=action['translation_m'], delta_ori_scale=action['rotation_rad'],
                                      damping=action['damping'], joint_limit_weight=action['joint_limit_weight'], max_dq=.01),
                'gripper': GripperCfg(entity_name='robot')},
            observations={'policy': ObservationGroupCfg(terms={
                'nodes': ObservationTermCfg(func=terms.nodes),
                'node_mask': ObservationTermCfg(func=terms.node_mask),
                'arm_task_state': ObservationTermCfg(func=terms.arm_state)}, concatenate_terms=False)},
            events={'reset': EventTermCfg(func=terms.reset, mode='reset')},
            metrics={'physics': MetricsTermCfg(func=terms.substep, per_substep=True)},
            rewards={'task': RewardTermCfg(func=terms.reward, weight=1.)},
            terminations={'task': TerminationTermCfg(func=terms.terminated),
                          'timeout': TerminationTermCfg(func=terms.timeout, time_out=True)})
        super().__init__(cfg, device=f'cuda:{gpu}')

    def load_managers(self):
        from .terms import TaskState
        self.task = TaskState(self)
        super().load_managers()

    def step(self, action):
        self.task.begin_step(action)
        return super().step(action)
