"""Test alternate torso/arm configurations for ONE recorded failed target pose.

No physics execution; removing collisions only diagnoses kinematic reachability.
"""
import argparse,copy,os
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--attempt',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--gpu',type=int,required=True);a=p.parse_args();os.environ.update(CUDA_VISIBLE_DEVICES=str(a.gpu),MUJOCO_GL='egl',MUJOCO_EGL_DEVICE_ID=str(a.gpu));a.out.mkdir(parents=True,exist_ok=False)
import yaml,torch
from curobo.types.base import TensorDeviceType
from curobo.types.math import Pose
from curobo.wrap.reacher.ik_solver import IKSolver,IKSolverConfig
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.planning.curobo import locked_arm_config,pose7
from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station,robot_contacts
from lastmile_dataflow.io import read_json,write_json
scene=a.attempt.parent.parent;f=read_json(scene.parent.parent/'frozen_config.json');packet=read_json(a.attempt/'config.json');goal=np.array(read_json(a.attempt/'planning.json')[-1]['goal_world']);directory=sorted(a.attempt.glob('dry_plans-*/*'),key=lambda x:x.stat().st_mtime_ns)[-1];ref=np.array(read_json(directory/'mobile_base.json')['reference_world']);root=Path(f['assets_dir'])/'robots/rby1m/curobo_config';original=yaml.safe_load((root/'rby1m_holobase.yml').read_text());sim=Simulation.from_snapshot(scene/'scene',RobotConfig(**f['robot']),target=packet['task']['target_body']);args=TensorDeviceType();target=Pose.from_list(pose7(np.linalg.solve(ref,goal)),args);rows=[]
try:
    for h in [0.,.369,.738]:
        place_frozen_station(sim,packet['station']);initial=copy.deepcopy(packet['station']['initial']);initial['torso']=[h];sim.robot.initialize(initial)
        contacts=robot_contacts(sim)
        for side in ['left','right']:
            cfg=locked_arm_config(original,sim,side,root,mobile_base=True)['robot_cfg'];cfg['kinematics']['urdf_path']=str((directory/'mobile_holobase.urdf').resolve())
            solver=IKSolver(IKSolverConfig.load_from_robot_config(cfg,world_model=None,tensor_args=args,num_seeds=128,use_cuda_graph=False,self_collision_check=False,self_collision_opt=False,seed=packet['seed']))
            r=solver.solve_single(target,return_seeds=8);row={'h':h,'arm':side,'freeIK_success':bool(r.success.any().item()),'position_error':r.position_error.detach().cpu().numpy().tolist(),'rotation_error':r.rotation_error.detach().cpu().numpy().tolist(),'pose_collisions':contacts}
            rows.append(row);print(row,flush=True);del solver;torch.cuda.empty_cache()
    write_json(a.out/'sweep.json',{'attempt':str(a.attempt.resolve()),'scope':'ONE recorded target-pose free IK sweep with 128 seeds; not collision-safe execution','rows':rows})
finally:sim.close()
