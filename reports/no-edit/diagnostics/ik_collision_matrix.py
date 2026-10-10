"""Isolated IK constraint ablations. NEVER executes/edits an active collection.

Restores recorded scenes into NEW unstarted Simulation objects; all collision
constraints disabled here are DIAGNOSTIC ONLY. A passing ablation is not a safe
plan or a physical grasp. Original attempt files remain read-only.
"""
import argparse,copy,json,os,time
from pathlib import Path
import numpy as np

def main():
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--gpu',type=int,required=True);a=p.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(a.gpu),MUJOCO_GL='egl',MUJOCO_EGL_DEVICE_ID=str(a.gpu))
    import mujoco,torch,yaml
    from curobo.types.base import TensorDeviceType
    from curobo.types.math import Pose
    from curobo.wrap.reacher.ik_solver import IKSolver,IKSolverConfig
    from lastmile_dataflow.config import RobotConfig
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.planning.curobo import collision_world,pose7
    from lastmile_dataflow.stations.no_edit_sampling import robot_contacts
    from lastmile_dataflow.io import read_json,write_json
    # Unique diagnostic output; forbid overwriting even diagnostic evidence.
    a.out.mkdir(parents=True,exist_ok=False)
    scene=a.attempt.parent.parent;run=scene.parent.parent;f=read_json(run/'frozen_config.json')
    plans=read_json(a.attempt/'planning.json');failed=[x for x in plans if x['status']=='no_solution']
    if not failed:raise ValueError('No failed plan')
    plan=failed[-1];goal=np.asarray(plan['goal_world'])
    dirs=list(a.attempt.glob('measured_planner-*')) if plan['phase'] not in ('dry_pregrasp','initialize') else list(a.attempt.glob('dry_plans-*/*'))
    # Last failed dry planner is the persisted diagnostic; measured uses one planner.
    dirs=sorted(dirs,key=lambda x:x.stat().st_mtime_ns);directory=dirs[-1]
    cfg=yaml.safe_load((directory/'planner.yml').read_text())['robot_cfg'];mobile=read_json(directory/'mobile_base.json');ref=np.asarray(mobile['reference_world'])
    side='left' if 'left' in cfg['kinematics']['ee_link'] else 'right'
    packet=read_json(a.attempt/'config.json');task=packet['task']
    sim=Simulation.from_snapshot(scene/'scene',RobotConfig(**f['robot']),target=task['target_body'])
    try:
        # Rebuild planner's original world at its construction state, not a new house.
        if (a.attempt/'replay.npz').exists():
            with np.load(a.attempt/'replay.npz') as z:
                ids=np.flatnonzero(z['phases']=='torso_adjust');index=int(ids[-1]) if len(ids) else 0
                sim.data.qpos[:]=z['qpos'][index];sim.data.time=float(z['times'][index]);mujoco.mj_forward(sim.model,sim.data)
        else:
            from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station
            place_frozen_station(sim,packet['station'])
            initial=copy.deepcopy(packet['station']['initial']);initial['torso']=[float(np.clip(cfg['kinematics']['lock_joints']['torso_1'],0.,.738))];sim.robot.initialize(initial)
        original=sim.data.qpos.copy()
        world=collision_world(sim,a.out,5.,reference_base=ref)
        args=TensorDeviceType();target=Pose.from_list(pose7(np.linalg.solve(ref,goal)),args)
        results=[]
        for seeds in [32,128]:
            for env,selfcheck in [(False,False),(False,True),(True,False),(True,True)]:
                start=time.monotonic()
                settings=IKSolverConfig.load_from_robot_config(copy.deepcopy(cfg),world_model=world if env else None,tensor_args=args,num_seeds=seeds,use_cuda_graph=False,self_collision_check=selfcheck,self_collision_opt=selfcheck,seed=packet['seed'],collision_cache={'mesh':3,'obb':512},collision_activation_distance=.01)
                solver=IKSolver(settings);r=solver.solve_single(target,return_seeds=min(8,seeds))
                success=r.success.detach().cpu().numpy().reshape(-1);q=r.solution.detach().cpu().numpy().reshape(-1,len(cfg['kinematics']['cspace']['joint_names']))
                row={'environment':env,'self_collision':selfcheck,'num_seeds':seeds,'success':bool(success.any()),'successful_returned':int(success.sum()),'time_s':time.monotonic()-start,'position_error':r.position_error.detach().cpu().numpy().tolist(),'rotation_error':r.rotation_error.detach().cpu().numpy().tolist()}
                if success.any():
                    point=q[np.flatnonzero(success)[0]];local=np.eye(4);local[:2,3]=point[:2];c,s=np.cos(point[2]),np.sin(point[2]);local[:2,:2]=[[c,-s],[s,c]];actual=ref@local
                    sim.data.qpos[:]=original;mujoco.mj_forward(sim.model,sim.data)
                    initial=copy.deepcopy(sim.robot.config.initial)
                    for key in ['base','head','left_arm','right_arm']:initial[key]=sim.robot.group(key).tolist()
                    initial['base']=[*actual[:2,3],float(np.arctan2(actual[1,0],actual[0,0]))];initial[side+'_arm']=point[3:].tolist();initial['torso']=[float(np.clip(cfg['kinematics']['lock_joints']['torso_1'],0.,.738))]
                    sim.robot.initialize(initial)
                    row.update(solution=point.tolist(),diagnostic_mujoco_collisions=robot_contacts(sim))
                    np.savez_compressed(a.out/f'ik-{seeds}-{int(env)}-{int(selfcheck)}.npz',q=point,spheres=solver.kinematics.get_state(args.to_device(point).view(1,-1)).get_link_spheres().detach().cpu().numpy())
                results.append(row);print(json.dumps(row),flush=True)
                write_json(a.out/'matrix.json',{'attempt':str(a.attempt.resolve()),'planner_directory':str(directory.resolve()),'failed_phase':plan['phase'],'goal_world':goal.tolist(),'scope':'IK ablation in new unstarted diagnostic sim, no physical execution; finite seeds','rows':results})
                del solver,settings;torch.cuda.empty_cache()
    finally:sim.close()
if __name__=='__main__':main()
