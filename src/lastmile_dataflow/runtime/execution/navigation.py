"""Closed-loop navigation shared by the two execution backends."""
from dataclasses import asdict
import numpy as np
from ..control.interface import RobotCommand
from ...planning.navigation.geometry import yaw_gap

from .context import OperationFailure

def navigate(ctx,path,goal):
    sim=ctx.sim; cfg=ctx.config; ctx.phase='navigation'; ctx.monitor=None; ctx.fingers=set()
    held={side:sim.robot.arm_positions(side).copy() for side in sim.robot.arms}
    count=0
    # Adapter retains the locked lift/torso and gripper targets across base commands.
    for pose in path['poses']:
        target=np.asarray(pose, float)
        while True:
            current=sim.robot.base_pose(); delta=target-current; delta[2]=yaw_gap(target[2],current[2])
            if np.linalg.norm(delta[:2]) <= cfg.arrival_tolerance_m and abs(delta[2]) <= cfg.arrival_tolerance_rad: break
            count+=1
            if count > cfg.max_navigation_steps: raise OperationFailure('navigation_tracking_timeout')
            limit=min(sim.robot.config.max_base_delta,cfg.navigation_speed_m_s/sim.robot.config.control_hz)
            yaw_limit=min(sim.robot.config.max_yaw_delta,cfg.navigation_yaw_speed_rad_s/sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            ctx.tick(RobotCommand(base_xyyaw=current+delta/scale, arm_q=held))
    ctx.phase='arrival'
    for _ in range(10):
        delta=np.asarray(goal['base'])-sim.robot.base_pose()
        delta[2]=yaw_gap(goal['base'][2],sim.robot.base_pose()[2])
        if np.linalg.norm(delta[:2]) > sim.robot.config.max_base_delta or abs(delta[2]) > sim.robot.config.max_yaw_delta:
            raise OperationFailure('arrival_tracking_error')
        ctx.tick(RobotCommand(base_xyyaw=sim.robot.base_pose()+delta, arm_q=held))
    actual=sim.robot.base_pose()
    xy_error=float(np.linalg.norm(actual[:2]-np.asarray(goal['base'])[:2]))
    yaw_error=abs(yaw_gap(goal['base'][2],actual[2]))
    if xy_error > cfg.arrival_tolerance_m or yaw_error > cfg.arrival_tolerance_rad:
        raise OperationFailure('arrival_tracking_error')
    return {'status':'success','actual_base':actual.tolist(),'steps':count,
            **asdict(ctx.navigation_contacts),
            'xy_error_m':xy_error,'yaw_error_rad':yaw_error,
            'arrival_tolerance_m':cfg.arrival_tolerance_m,'arrival_tolerance_rad':cfg.arrival_tolerance_rad}

