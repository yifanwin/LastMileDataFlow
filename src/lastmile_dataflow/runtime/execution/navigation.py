"""Closed-loop navigation shared by the two execution backends."""
import numpy as np

from .context import OperationFailure

def navigate(ctx,path,goal):
    sim=ctx.sim; cfg=ctx.config; ctx.phase='navigation'; ctx.monitor=None; ctx.fingers=set()
    held={side:sim.robot.group(side+'_arm').copy() for side in ('left','right')}
    start_yaw=sim.robot.group('base')[2]; yaw_goal=goal['base'][2]; points=path['xy']; count=0
    # Same closed-loop torso command as arm_tick/mobile_tick: hold the last legal
    # target, re-derive it from feedback so gravity sag is corrected instead of
    # accumulating. A raw absolute command (or neutral_action's ctrl echo) is open
    # loop and lets the measured h drift below the lower bound.
    hold=ctx.hold_torso_target
    for index,xy in enumerate(points):
        fraction=index/max(1,len(points)-1)
        yaw=start_yaw+fraction*ctx.yaw_gap(yaw_goal,start_yaw)
        target=np.r_[xy,yaw]
        while True:
            current=sim.robot.group('base'); delta=target-current; delta[2]=ctx.yaw_gap(target[2],current[2])
            if np.linalg.norm(delta[:2]) <= cfg.arrival_tolerance_m and abs(delta[2]) <= cfg.arrival_tolerance_rad: break
            count+=1
            if count > cfg.max_navigation_steps: raise OperationFailure('navigation_tracking_timeout')
            limit=min(sim.robot.config.max_base_delta,cfg.navigation_speed_m_s/sim.robot.config.control_hz)
            yaw_limit=min(sim.robot.config.max_yaw_delta,cfg.navigation_yaw_speed_rad_s/sim.robot.config.control_hz)
            scale=max(1.,np.linalg.norm(delta[:2])/limit,abs(delta[2])/yaw_limit)
            a=sim.robot.neutral_action(); a[:3]=delta/scale
            a[3:10]=held['left']-sim.robot.group('left_arm'); a[11:18]=held['right']-sim.robot.group('right_arm')
            a[19]=hold()
            ctx.tick(a)
    ctx.phase='arrival'
    for _ in range(10):
        a=sim.robot.neutral_action(); delta=np.asarray(goal['base'])-sim.robot.group('base')
        delta[2]=ctx.yaw_gap(goal['base'][2],sim.robot.group('base')[2])
        if np.linalg.norm(delta[:2]) > sim.robot.config.max_base_delta or abs(delta[2]) > sim.robot.config.max_yaw_delta:
            raise OperationFailure('arrival_tracking_error')
        a[:3]=delta
        a[19]=hold()
        ctx.tick(a)
    actual=sim.robot.group('base')
    xy_error=float(np.linalg.norm(actual[:2]-np.asarray(goal['base'])[:2]))
    yaw_error=abs(ctx.yaw_gap(goal['base'][2],actual[2]))
    if xy_error > cfg.arrival_tolerance_m or yaw_error > cfg.arrival_tolerance_rad:
        raise OperationFailure('arrival_tracking_error')
    return {'status':'success','actual_base':actual.tolist(),'steps':count,
            'xy_error_m':xy_error,'yaw_error_rad':yaw_error,
            'arrival_tolerance_m':cfg.arrival_tolerance_m,'arrival_tolerance_rad':cfg.arrival_tolerance_rad}

