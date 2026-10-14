"""Explicit V2 command projection. Never changes qpos or the model limits."""
import numpy as np
from .action import InvalidAction, vector, validate_action

ANGULAR_TOLERANCE_RAD = .003
LINEAR_TOLERANCE_M = .001


def joint_command_interval(robot, group, index):
    name = robot.groups[group][index]
    joint = robot.joints[name]
    lo, hi = -np.inf, np.inf
    if robot.model.jnt_limited[joint]:
        lo, hi = map(float, robot.model.jnt_range[joint])
    if index < len(robot.actuators[group]):
        actuator = robot.act_ids[robot.actuators[group][index]]
        if robot.model.actuator_ctrllimited[actuator]:
            alo, ahi = robot.model.actuator_ctrlrange[actuator]
            lo, hi = max(lo, float(alo)), min(hi, float(ahi))
    if lo > hi:
        raise ValueError('inconsistent joint and actuator limits: '+name)
    return lo, hi


def yaw_interval(robot):
    return joint_command_interval(robot, 'base', 2)


def project(value, limits, tolerance, name, events):
    lo, hi = limits
    if not np.isfinite(value) or value < lo-tolerance or value > hi+tolerance:
        raise InvalidAction(f'command limit: {name}={value}, [{lo}, {hi}]')
    bounded = float(np.clip(value, lo, hi))
    if bounded != value:
        events.append({'joint':name, 'input_target':float(value), 'submitted_target':bounded,
                       'limits':[lo,hi], 'tolerance':tolerance})
    return bounded


def sanitize_action(robot, value, *, fixed_base=False):
    """Intersect joint/actuator/protocol limits; project only tiny overshoots.

    Yaw is a LIMITED scalar in this model, not a continuous joint. Never wrap a
    command across +-pi (that would require an illegal ~2pi joint increment).
    """
    a = vector(value,20).copy()
    events=[]
    for group, offset, count in [('base',0,3),('left_arm',3,7),('right_arm',11,7)]:
        measured=robot.group(group)
        for index in range(count):
            limits=joint_command_interval(robot,group,index)
            tolerance=LINEAR_TOLERANCE_M if group=='base' and index<2 else ANGULAR_TOLERANCE_RAD
            target=project(float(measured[index]+a[offset+index]),limits,tolerance,
                           robot.groups[group][index],events)
            a[offset+index]=target-measured[index]
    for side,offset in [('left',10),('right',18)]:
        group=side+'_gripper'
        lo,hi=joint_command_interval(robot,group,0)
        plo,phi=joint_command_interval(robot,group,1)
        lo,hi=max(lo,-phi,robot.config.gripper_limits[0]),min(hi,-plo,robot.config.gripper_limits[1])
        a[offset]=project(a[offset],(lo,hi),LINEAR_TOLERANCE_M,group,events)
    lo,hi=robot.config.torso_limits
    for index, multiplier in [(1,1),(2,-2),(3,1)]:
        lower,upper=joint_command_interval(robot,'torso',index)
        lower,upper=sorted((lower/multiplier,upper/multiplier))
        lo,hi=max(lo,lower),min(hi,upper)
    a[19]=project(a[19],(lo,hi),ANGULAR_TOLERANCE_RAD,'torso_h',events)
    # Head and torso zero joints have no free command channel: check, don't edit.
    robot._check_joint(robot.groups['torso'][0],0.)
    robot._check_joint(robot.groups['torso'][4],0.)
    robot._check_joint(robot.groups['torso'][5],0.)
    validate_action(a,robot.config,fixed_base=fixed_base)
    return a,events


def local_yaw_bounds(robot, reference_yaw):
    lo,hi=yaw_interval(robot)
    return lo-reference_yaw,hi-reference_yaw
