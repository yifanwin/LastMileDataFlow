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

from .scene import ENTITY_PREFIX, ROBOT_ENTITY, ROBOT_PREFIX, RLSceneConfig, prepare_rl_scene
from lastmile_dataflow.robots.interface import ROBOT_NAMESPACE
from .dls import BaseFrameDLSCfg
from .mobile import MobileBaseCfg
from . import terms
from lastmile_dataflow.operation.outcomes.pick_state import RL_PICK_THRESHOLDS
from .terms import TaskState


@dataclass(kw_only=True)
class GripperCfg(ActionTermCfg):
    positive_closes: bool = False
    # Closed-fraction change per policy step at |action| = 1; None keeps absolute width commands.
    step: float | None = None

    def build(self, env):
        return Gripper(self, env)


class Gripper(ActionTerm):
    """One normalized width controls the two signed Panda finger slides."""

    def __init__(self, cfg, env):
        super().__init__(cfg, env)
        self.ids, _ = self._entity.find_joints(
            ["gripper0_right_finger_joint1", "gripper0_right_finger_joint2"], preserve_order=True
        )
        self.lower = self._entity.data.joint_pos_limits[:, self.ids, 0]
        self.upper = self._entity.data.joint_pos_limits[:, self.ids, 1]
        self.open_targets = torch.stack((self.upper[:, 0], self.lower[:, 1]), -1)
        self.closed_targets = torch.stack((self.lower[:, 0], self.upper[:, 1]), -1)
        self.positive_closes = cfg.positive_closes
        self.step = cfg.step
        self.closed = torch.zeros(self.num_envs, 1, device=self.device)
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
        closing = actions if self.positive_closes else -actions
        if self.step is None:
            self.closed[:] = (1 + closing) / 2
        else:
            self.closed[:] = (self.closed + self.step * closing).clamp(0, 1)
        self.targets[:] = self.open_targets + self.closed * (self.closed_targets - self.open_targets)

    def apply_actions(self):
        self._entity.set_joint_position_target(self.targets, joint_ids=self.ids)

    def reset(self, env_ids=None):
        ids = slice(None) if env_ids is None else env_ids
        self._raw[ids] = 0
        self.closed[ids] = 0
        self.targets[ids] = self.open_targets[ids]


def contact_sensor(name, primary, secondary=None):
    return ContactSensorCfg(
        name=name, primary=primary, secondary=secondary, fields=("found", "force"), reduce="maxforce"
    )


def typed_contact_sensors(prepared, target, robot):
    """Pads, finger bodies with normals, finger pair and table contacts for typed collision penalties."""
    fingers = [ContactMatch(mode="subtree", pattern=ROBOT_PREFIX + f"gripper0_right_{side}finger")
               for side in ("left", "right")]
    table = ContactMatch(mode="subtree", pattern=prepared.support_body)
    sensors = [contact_sensor("fpair", fingers[0], fingers[1]),
               contact_sensor("table_robot", robot, table)]
    for index in (0, 1):
        geom = ROBOT_PREFIX + f"gripper0_right_finger{index + 1}"
        sensors += [
            contact_sensor(f"pad_{index}", ContactMatch(mode="geom", pattern=geom + "_pad_collision"), target),
            ContactSensorCfg(name=f"fmesh_{index}", primary=ContactMatch(mode="geom", pattern=geom + "_collision"),
                             secondary=target, fields=("found", "normal"), reduce="maxforce"),
            contact_sensor(f"ftable_{index}", fingers[index], table),
        ]
    return sensors


def build_sensors(prepared, typed=False):
    target = ContactMatch(mode="subtree", pattern=prepared.target_body)
    robot = ContactMatch(mode="subtree", pattern=ROBOT_PREFIX + "robot0_base")
    sensors = [contact_sensor("self_collision", robot, robot)]
    if typed:
        if prepared.support_body not in [body.name for body in prepared.scene_spec.worldbody.bodies]:
            raise ValueError("Typed collisions need the support table as a top-level scene body")
        sensors += typed_contact_sensors(prepared, target, robot)
    for index, finger in enumerate(("left", "right")):
        sensors.append(
            contact_sensor(
                f"finger_{index}",
                ContactMatch(mode="subtree", pattern=ROBOT_PREFIX + f"gripper0_right_{finger}finger"),
                target,
            )
        )
    for index, body in enumerate(prepared.scene_spec.worldbody.bodies):
        if body.name == prepared.target_body:
            continue
        other = ContactMatch(mode="subtree", pattern=body.name)
        sensors.append(contact_sensor(f"support_{index}", target, other))
        # Typed penalties split the support table into finger and arm contacts.
        if not (typed and body.name == prepared.support_body):
            sensors.append(contact_sensor(f"environment_{index}", robot, other))
    for index, geom in enumerate(prepared.scene_spec.worldbody.geoms):
        if not (geom.contype or geom.conaffinity):
            continue
        other = ContactMatch(mode="geom", pattern=geom.name)
        sensors.append(contact_sensor(f"support_world_{index}", target, other))
        # Normal base-floor contact is excluded, while the arm remains monitored.
        primary = (
            ContactMatch(mode="subtree", pattern=ROBOT_PREFIX + "mobilebase0_support")
            if "floor" in geom.name.lower()
            else robot
        )
        sensors.append(contact_sensor(f"environment_world_{index}", primary, other))
    # Nonfinger contacts with the cup are illegal, without penalizing grasping.
    for index, body in enumerate(prepared.robot_spec.bodies):
        if "finger" not in body.name and body.geoms:
            sensors.append(
                contact_sensor(
                    f"nonfinger_{index}", ContactMatch(mode="body", pattern=ENTITY_PREFIX + body.name), target
                )
            )
    return tuple(sensors)


class StationEnv(ManagerBasedRlEnv):
    def __init__(self, config, *, gpu, seed, num_envs=None):

        contact_protocol = config["success"].get("version") == "rl-pick-contact-v2"
        if not contact_protocol:
            for key, value in config["success"].items():
                if RL_PICK_THRESHOLDS[key] != value:
                    raise ValueError("RL success configuration must match the named protocol")
        mobile = config["action"].get("mobile_base", False)
        if mobile and (config["scene"].get("fixed_base", False) or not contact_protocol
                       or not config.get("observation", {}).get("world_state", False)):
            raise ValueError("Mobile base requires a dynamic base and flat contact observations with world_state")
        self.research_config = config
        self.prepared = prepare_rl_scene(RLSceneConfig(**config["scene"]))
        self.task = None
        prepared = self.prepared
        initial = {
            name.split("/")[-1]: value
            for name, value in prepared.initial_qpos.items()
            if name.startswith(ROBOT_NAMESPACE)
        }
        root = prepared.robot_spec.bodies[1]
        robot = EntityCfg(
            spec_fn=prepared.copy_robot,
            init_state=EntityCfg.InitialStateCfg(
                pos=(0.0, 0.0, 0.0) if config["scene"].get("fixed_base", False) else tuple(root.pos),
                rot=tuple(root.quat), joint_pos=initial,
            ),
            articulation=EntityArticulationInfoCfg(actuators=(XmlActuatorCfg(target_names_expr=tuple(initial)),)),
        )
        action = config["action"]
        env_cfg = config["environment"]
        strict = "termination" in config
        sensors = build_sensors(prepared, typed="collision_tiers" in config["reward"] or strict)
        observation_terms = (
            {"flat": ObservationTermCfg(func=terms.flat)} if contact_protocol
            else {
                "nodes": ObservationTermCfg(func=terms.nodes),
                "node_mask": ObservationTermCfg(func=terms.node_mask),
                "arm_task_state": ObservationTermCfg(func=terms.arm_state),
            }
        )
        cfg = ManagerBasedRlEnvCfg(
            scene=SceneCfg(
                num_envs=num_envs or env_cfg["num_envs"],
                env_spacing=0.0,
                entities={ROBOT_ENTITY: robot},
                sensors=sensors,
                spec_fn=prepared.attach_scene,
            ),
            sim=SimulationCfg(
                nconmax=env_cfg["nconmax"],
                njmax=env_cfg["njmax"],
                mujoco=MujocoCfg(
                    timestep=config["scene"]["physics_dt"],
                    impratio=10.0,
                    cone="elliptic",
                    gravity=(0.0, 0.0, -9.8),
                    disableflags=() if prepared.multiccd else ("multiccd",),
                ),
            ),
            seed=seed,
            decimation=env_cfg["decimation"],
            episode_length_s=env_cfg["episode_s"],
            auto_reset=False,
            is_finite_horizon=True,
            scale_rewards_by_dt=False,
            actions={
                "tcp": BaseFrameDLSCfg(
                    entity_name=ROBOT_ENTITY,
                    actuator_names=[f"robot0_joint{i}" for i in range(1, 8)] + ["mobilebase0_joint_torso_height"],
                    frame_type="site",
                    frame_name="gripper0_right_grip_site",
                    base_site=ROBOT_PREFIX + "base_site",
                    moving_base=mobile,
                    delta_pos_scale=action["translation_m"],
                    delta_ori_scale=action["rotation_rad"],
                    damping=action["damping"],
                    joint_limit_weight=action["joint_limit_weight"],
                    joint_velocity_limits=(2.175, 2.175, 2.175, 2.175, 2.61, 2.61, 2.61, action["lift_velocity_m_s"]),
                ),
                "gripper": GripperCfg(entity_name=ROBOT_ENTITY, positive_closes=contact_protocol,
                                      step=action.get("gripper_step")),
                **({"base": MobileBaseCfg(
                    entity_name=ROBOT_ENTITY,
                    translation_m=action["base_translation_m"],
                    yaw_rad=action["base_yaw_rad"],
                )} if mobile else {}),
            },
            observations={
                "policy": ObservationGroupCfg(
                    terms=observation_terms,
                    concatenate_terms=False,
                )
            },
            events={"reset": EventTermCfg(func=terms.reset, mode="reset")},
            metrics={"physics": MetricsTermCfg(func=terms.substep, per_substep=True)},
            rewards={"task": RewardTermCfg(func=terms.reward, weight=1.0)},
            terminations={
                "task": TerminationTermCfg(func=terms.terminated),
                # With strict termination the task time limit is a true terminal (remaining time is observed).
                "timeout": TerminationTermCfg(func=terms.timeout, time_out=not strict),
            },
        )
        super().__init__(cfg, device=f"cuda:{gpu}")

    def load_managers(self):

        self.task = TaskState(self)
        super().load_managers()

    def step(self, action):
        self.task.begin_step(action)
        return super().step(self.task.execute_action(action))
