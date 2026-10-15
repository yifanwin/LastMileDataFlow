"""Versioned native MolmoBot 20D bridge; never reinterpret motor commands as metres."""
import numpy as np

from .action import InvalidAction, torso_joints, vector
from .action_limits import joint_command_interval

PROTOCOL = 'rby1-multitask-molmobot-native-v1'


def apply_native(robot, value, *, fixed_base=False):
    raw = vector(value, 20)
    if fixed_base:
        raise InvalidAction('native mobile policy cannot be used in fixed-base protocol')
    # Existing hard per-command safety bounds remain explicit, not hidden scaling.
    c = robot.config
    if np.linalg.norm(raw[:2]) > c.max_base_delta or abs(raw[2]) > c.max_yaw_delta:
        raise InvalidAction('native base delta exceeds safety bound')
    if np.max(np.abs(np.r_[raw[3:10], raw[11:18]])) > c.max_arm_delta:
        raise InvalidAction('native arm delta exceeds safety bound')
    targets = {}; adjustments = []; stationary = {}
    prior = robot._native_stationary_targets
    prior_stationary = robot._native_stationary
    for group, offset, count in [('base', 0, 3), ('left_arm', 3, 7), ('right_arm', 11, 7)]:
        delta = raw[offset:offset+count]
        # Upstream JointRelPosController retains a stationary target for zero commands.
        stationary[group] = np.linalg.norm(delta) < 1e-6
        if stationary[group]:
            # A moving -> stationary transition latches the measured position,
            # not the previous (possibly unreached) commanded target.
            target = (prior[group] if prior_stationary.get(group) and group in prior
                      else robot.group(group)).copy()
        else:
            target = robot.group(group) + delta
        bounded = np.array([np.clip(q, *joint_command_interval(robot, group, i))
                            for i, q in enumerate(target)])
        if not np.array_equal(target, bounded):
            adjustments.append({'group':group, 'input_target':target.tolist(), 'target':bounded.tolist()})
        targets[group] = bounded
    for side, offset in [('left', 10), ('right', 18)]:
        group = side + '_gripper'
        actuator = robot.act_ids[side + '_finger_act']
        limits = robot.model.actuator_ctrlrange[actuator]
        if not np.allclose(limits, [-100, 100]):
            raise InvalidAction('native motor limits changed')
        targets[group] = np.array([np.clip(raw[offset], *limits)])
        if targets[group][0] != raw[offset]:
            adjustments.append({'group':group, 'input_target':[float(raw[offset])],
                                'target':targets[group].tolist()})
    h = float(np.clip(raw[19], *c.torso_limits))  # upstream TorsoHeightJointPosController
    if h != raw[19]:
        adjustments.append({'group':'torso', 'input_target':[float(raw[19])], 'target':[h]})
    targets['torso'] = torso_joints(h)
    targets['head'] = robot.fixed_head.copy()
    for group, qs in targets.items():
        if group.endswith('gripper'):
            continue  # motor force/control target is not a finger joint position
        for name, q in zip(robot.groups[group], qs):
            robot._check_joint(name, q)
    ctrl = robot._controls(targets)
    # Atomic mutation only after all checks; stationary cache is also transactional.
    robot.data.ctrl[:] = ctrl
    robot._native_stationary_targets = {
        group: targets[group].copy() for group in ('base', 'left_arm', 'right_arm')}
    robot._native_stationary = stationary
    submitted = raw.copy()
    submitted[10] = targets['left_gripper'][0]
    submitted[18] = targets['right_gripper'][0]
    submitted[19] = h
    for group, offset in [('base', 0), ('left_arm', 3), ('right_arm', 11)]:
        count = len(targets[group])
        submitted[offset:offset+count] = targets[group] - robot.group(group)
    return {'protocol':PROTOCOL, 'submitted_action':submitted.tolist(),
            'joint_targets':{k:v.tolist() for k,v in targets.items() if not k.endswith('gripper')},
            'motor_targets':{k:v.tolist() for k,v in targets.items() if k.endswith('gripper')},
            'gripper_units':'native motor control, NOT metres', 'adjustments':adjustments,
            'actuator_names':[robot.model.actuator(i).name for i in range(robot.model.nu)],
            'ctrl':ctrl.tolist()}
