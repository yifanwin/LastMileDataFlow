"""Replay planning inputs only; never runs simulated controls or edits source attempts."""
import argparse,copy,os,time
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--gpu',type=int,required=True);a=p.parse_args();os.environ.update(CUDA_VISIBLE_DEVICES=str(a.gpu),MUJOCO_GL='egl',MUJOCO_EGL_DEVICE_ID=str(a.gpu));a.out.mkdir(parents=True,exist_ok=False)
import mujoco,yaml,torch
from curobo.types.base import TensorDeviceType
from curobo.types.math import Pose
from curobo.types.robot import JointState
from curobo.geom.sdf.world import CollisionCheckerType
from curobo.wrap.reacher.motion_gen import MotionGen,MotionGenConfig,MotionGenPlanConfig
from curobo.wrap.reacher.ik_solver import IKSolver,IKSolverConfig
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.planning.curobo import collision_world,pose7,body_pose
from lastmile_dataflow.io import read_json,write_json
scene=a.attempt.parent.parent;f=read_json(scene.parent.parent/'frozen_config.json');directory=next(a.attempt.glob('measured_planner-*'));cfg=yaml.safe_load((directory/'planner.yml').read_text())['robot_cfg'];ref=np.array(read_json(directory/'mobile_base.json')['reference_world']);packet=read_json(a.attempt/'config.json');plans=read_json(a.attempt/'planning.json');sim=Simulation.from_snapshot(scene/'scene',RobotConfig(**f['robot']),target=packet['task']['target_body']);args=TensorDeviceType();names=cfg['kinematics']['cspace']['joint_names'];side='left' if 'left' in cfg['kinematics']['ee_link'] else 'right';results=[]
def qnow():
    b=np.linalg.solve(ref,body_pose(sim,sim.model.body('robot_0/base').id));return np.r_[b[:2,3],np.arctan2(b[1,0],b[0,0]),sim.robot.group(side+'_arm')]
def summarize(r):
    return {'success':bool(r.success.any().item()),'position_error':r.position_error.detach().cpu().numpy().tolist(),'rotation_error':r.rotation_error.detach().cpu().numpy().tolist()}
try:
    with np.load(a.attempt/'replay.npz') as z:
        index=int(np.flatnonzero(z['phases']=='torso_adjust')[-1]);sim.data.qpos[:]=z['qpos'][index];mujoco.mj_forward(sim.model,sim.data)
    initial=qnow();world=collision_world(sim,a.out,5.,reference_base=ref)
    sim.restore_snapshot(a.attempt/'final_snapshot.npz');final=qnow()
    mgcfg=MotionGenConfig.load_from_robot_config(copy.deepcopy(cfg),world,args,collision_checker_type=CollisionCheckerType.MESH,use_cuda_graph=False,trajopt_tsteps=20,interpolation_dt=.05,collision_cache={'mesh':3,'obb':512},collision_activation_distance=.01,fixed_iters_trajopt=True,maximum_trajectory_dt=.5,self_collision_check=True,self_collision_opt=True,num_ik_seeds=32,num_trajopt_seeds=4,store_debug_in_result=True)
    motion=MotionGen(mgcfg);motion.warmup(enable_graph=False,warmup_js_trajopt=False)
    options=MotionGenPlanConfig(enable_graph=False,max_attempts=3,enable_graph_attempt=None,enable_finetune_trajopt=True,parallel_finetune=True,time_dilation_factor=.5,num_ik_seeds=32,num_trajopt_seeds=4,check_start_validity=True)
    for phase,start in [('pregrasp',initial),('approach',final)]:
        goal=next(x['goal_world'] for x in plans if x['phase']==phase);target=Pose.from_list(pose7(np.linalg.solve(ref,np.array(goal))),args);t=time.monotonic();r=motion.plan_single(JointState.from_position(args.to_device(start).view(1,-1),names),target,options)
        row={'phase':phase,'mode':'recorded MotionGen sequence','success':bool(r.success.any().item()),'status':str(r.status),'time_s':time.monotonic()-t}
        if r.debug_info and 'ik_result' in r.debug_info:row['ik']=summarize(r.debug_info['ik_result'])
        results.append(row);print(row,flush=True)
    cached=motion.ik_solver._goal_buffer.links_goal_pose
    results.append({'mode':'cached world link goals after original MotionGen plans','keys':list(cached) if cached is not None else [],'poses':{k: {'position':v.position.detach().cpu().numpy().tolist(),'quaternion':v.quaternion.detach().cpu().numpy().tolist()} for k,v in (cached or {}).items()}})
    cfg_trim=copy.deepcopy(cfg)
    trimmed=MotionGen(MotionGenConfig.load_from_robot_config(cfg_trim,world,args,collision_checker_type=CollisionCheckerType.MESH,use_cuda_graph=False,trajopt_tsteps=20,interpolation_dt=.05,collision_cache={'mesh':3,'obb':512},collision_activation_distance=.01,fixed_iters_trajopt=True,maximum_trajectory_dt=.5,self_collision_check=True,self_collision_opt=True,num_ik_seeds=32,num_trajopt_seeds=4,store_debug_in_result=True))
    saved_plan_single=trimmed.plan_single
    def no_aux_goals(*pa,**kw):
        kw['link_poses']=None
        return saved_plan_single(*pa,**kw)
    trimmed.plan_single=no_aux_goals
    trimmed.warmup(enable_graph=False,warmup_js_trajopt=False)
    for phase,start in [('pregrasp',initial),('approach',final)]:
        gg=next(x['goal_world'] for x in plans if x['phase']==phase);tg=Pose.from_list(pose7(np.linalg.solve(ref,np.array(gg))),args)
        rr=trimmed.plan_single(JointState.from_position(args.to_device(start).view(1,-1),names),tg,options)
        row={'phase':phase,'mode':'MotionGen warmup auxiliary world link goals suppressed, ALL joint locks and collision constraints retained','success':bool(rr.success.any().item()),'status':str(rr.status)}
        if rr.success.any().item():
            points=rr.get_interpolated_plan().position.detach().cpu().numpy();row['planned_points']=len(points);row['base_span']=np.ptp(points[:,:3],axis=0).tolist();np.savez_compressed(a.out/f'trimmed-{phase}.npz',positions=points)
        results.append(row);print(row,flush=True)
    del trimmed;torch.cuda.empty_cache()
    for condition in [False,True]:
        kwargs={'retract_config':args.to_device(final).view(1,-1),'seed_config':args.to_device(final).view(1,1,-1)} if condition else {}
        r=motion.ik_solver.solve_single(target,return_seeds=4,**kwargs)
        row={'phase':'approach','mode':'MotionGen embedded IK','current_start_condition':condition,**summarize(r)};results.append(row);print(row,flush=True)
    for regularization,condition in [(True,False),(True,True),(False,True)]:
        settings=IKSolverConfig.load_from_robot_config(copy.deepcopy(cfg),world_model=world,tensor_args=args,num_seeds=32,use_cuda_graph=False,self_collision_check=True,self_collision_opt=True,seed=1531,regularization=regularization,collision_cache={'mesh':3,'obb':512},collision_activation_distance=.01)
        solver=IKSolver(settings);kwargs={'retract_config':args.to_device(final).view(1,-1),'seed_config':args.to_device(final).view(1,1,-1)} if condition else {};r=solver.solve_single(target,return_seeds=4,**kwargs);row={'phase':'approach','mode':'IK only','regularization':regularization,'current_start_condition':condition,**summarize(r)}
        if r.success.any().item():
            pt=r.solution.reshape(-1,len(names))[torch.nonzero(r.success.reshape(-1))[0,0]].view(1,-1)
            row['standalone_constraint_valid']=solver.check_valid(pt).detach().cpu().numpy().tolist()
            row['embedded_constraint_valid']=motion.ik_solver.check_valid(pt).detach().cpu().numpy().tolist()
            k1=solver.kinematics.get_state(pt);k2=motion.ik_solver.kinematics.get_state(pt)
            row['kinematic_sphere_max_difference']=float(torch.max(torch.abs(k1.get_link_spheres()-k2.get_link_spheres())).item())
        results.append(row);print(row,flush=True)
        if regularization and condition:
            previous=motion.ik_solver;motion.ik_solver=solver
            rr=motion.plan_single(JointState.from_position(args.to_device(final).view(1,-1),names),target,options)
            row2={'phase':'approach','mode':'fresh standalone IK injected diagnostically into MotionGen','success':bool(rr.success.any().item()),'status':str(rr.status)};results.append(row2);print(row2,flush=True)
            motion.ik_solver=previous
        del solver;torch.cuda.empty_cache()
    write_json(a.out/'probe.json',{'attempt':str(a.attempt.resolve()),'scope':'planning input replay only; not motion execution','results':results})
finally:sim.close()
