"""Versioned native MolmoBot 20D bridge; never reinterpret motor commands as metres."""

import numpy as np

from .action import (
    DELTA_GROUPS,
    GRIPPER_INDEX,
    TORSO_INDEX,
    InvalidAction,
    check_delta_bounds,
    joint_command_interval,
    torso_joints,
    vector,
)

PROTOCOL = "rby1-multitask-molmobot-native-v1"
NATIVE_MOTOR_LIMITS = (-100.0, 100.0)


class NativeControl:
    """Upstream JointRelPosController semantics, including stationary target latching."""

    def __init__(self):
        self.stationary_targets = {}
        self.stationary = {}

    def apply(self, robot, value, *, fixed_base=False):
        raw = vector(value, 20)
        if fixed_base:
            raise InvalidAction("native mobile policy cannot be used in fixed-base protocol")
        # Existing hard per-command safety bounds remain explicit, not hidden scaling.
        check_delta_bounds(raw, robot.config, "native ")
        targets, adjustments, stationary = {}, [], {}
        for group, part in DELTA_GROUPS:
            delta = raw[part]
            stationary[group] = np.linalg.norm(delta) < 1e-6
            if stationary[group]:
                # A moving -> stationary transition latches the measured position,
                # not the previous (possibly unreached) commanded target.
                latched = self.stationary.get(group) and group in self.stationary_targets
                target = (self.stationary_targets[group] if latched else robot.group(group)).copy()
            else:
                target = robot.group(group) + delta
            bounded = np.array([np.clip(q, *joint_command_interval(robot, group, i)) for i, q in enumerate(target)])
            if not np.array_equal(target, bounded):
                adjustments.append({"group": group, "input_target": target.tolist(), "target": bounded.tolist()})
            targets[group] = bounded
        for side, offset in GRIPPER_INDEX.items():
            group = side + "_gripper"
            limits = robot.model.actuator_ctrlrange[robot.joints.act_ids[side + "_finger_act"]]
            if not np.allclose(limits, NATIVE_MOTOR_LIMITS):
                raise InvalidAction("native motor limits changed")
            targets[group] = np.array([np.clip(raw[offset], *limits)])
            if targets[group][0] != raw[offset]:
                adjustments.append(
                    {"group": group, "input_target": [float(raw[offset])], "target": targets[group].tolist()}
                )
        h = float(np.clip(raw[TORSO_INDEX], *robot.config.torso_limits))  # upstream TorsoHeightJointPosController
        if h != raw[TORSO_INDEX]:
            adjustments.append({"group": "torso", "input_target": [float(raw[TORSO_INDEX])], "target": [h]})
        targets["torso"] = torso_joints(h)
        targets["head"] = robot.fixed_head.copy()
        for group, qs in targets.items():
            if group.endswith("gripper"):
                continue  # motor force/control target is not a finger joint position
            for name, q in zip(robot.joints.groups[group], qs):
                robot.joints.check_joint(robot.model, name, q)
        ctrl = robot.controls(targets)
        # Atomic mutation only after all checks; stationary cache is also transactional.
        robot.data.ctrl[:] = ctrl
        self.stationary_targets = {group: targets[group].copy() for group, _ in DELTA_GROUPS}
        self.stationary = stationary
        submitted = raw.copy()
        for side, offset in GRIPPER_INDEX.items():
            submitted[offset] = targets[side + "_gripper"][0]
        submitted[TORSO_INDEX] = h
        for group, part in DELTA_GROUPS:
            submitted[part] = targets[group] - robot.group(group)
        return {
            "protocol": PROTOCOL,
            "submitted_action": submitted.tolist(),
            "joint_targets": {k: v.tolist() for k, v in targets.items() if not k.endswith("gripper")},
            "motor_targets": {k: v.tolist() for k, v in targets.items() if k.endswith("gripper")},
            "gripper_units": "native motor control, NOT metres",
            "adjustments": adjustments,
            "actuator_names": [robot.model.actuator(i).name for i in range(robot.model.nu)],
            "ctrl": ctrl.tolist(),
        }
