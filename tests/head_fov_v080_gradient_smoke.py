"""Explicit CUDA FK/gradient/manager isolation test on the real RBY-1 model."""
import argparse
from pathlib import Path
import numpy as np
import torch
from curobo.types import JointState
from curobo._src.robot.kinematics.kinematics import Kinematics
from lastmile_dataflow.config import RobotConfig,construct
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.no_edit_execution import planner_options
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station
from lastmile_dataflow.planning.curobo_v2 import V2Planner
from lastmile_dataflow.planning.head_fov import target_camera_torch


def main():
    p=argparse.ArgumentParser();p.add_argument('--frozen-run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    f=read_json(args.frozen_run/'frozen_config.json');scene=args.frozen_run/'scenes/val_103'
    task_dir=next((scene/'tasks').glob('pick-*'));task=read_json(task_dir/'task.json')
    station=next(s for s in read_json(task_dir/'stations.json') if s['station_id']=='S0005')
    sim=Simulation.from_snapshot(scene/'scene',construct(RobotConfig,f['robot']),target=task['target_body']);planner=None
    try:
        place_frozen_station(sim,station)
        cfg=construct(NoEditConfig,{**f['config'],'head_fov_enabled':True})
        planner=V2Planner(sim,planner_options(cfg,f['assets_dir'],105880152),'left',args.output,workspace=(task['anchor_world'],cfg.radius_m))
        fk=Kinematics(planner.context['fov_robot'].kinematics,compute_spheres=False)
        q=planner.state().reorder(fk.joint_names).position.reshape(1,1,-1).repeat(2,3,1)
        q[...,fk.joint_names.index('base_theta')]=1.
        q[...,fk.joint_names.index('torso_1')]=.369
        q.requires_grad_(True)
        def loss(values):
            js=JointState.from_position(values,joint_names=fk.joint_names)
            return planner.context['fov_cost'](target_camera_torch(fk,js,planner.context))
        terms=loss(q);terms.sum().backward();gradient=q.grad.detach().clone()
        assert terms.shape==(2,3,1) and terms.is_cuda
        assert torch.isfinite(gradient).all() and gradient.abs().max()>0
        comparisons={}
        for name in ('base_x','base_y','base_theta','torso_1'):
            j=fk.joint_names.index(name);step=.001
            plus=q.detach().clone();minus=q.detach().clone();plus[0,1,j]+=step;minus[0,1,j]-=step
            positive=float(loss(plus).sum().item());negative=float(loss(minus).sum().item())
            finite=(positive-negative)/(2*step);actual=float(gradient[0,1,j].item())
            assert np.isclose(actual,finite,rtol=.025,atol=2.),(name,actual,finite)
            comparisons[name]={'autograd':actual,'finite_difference':finite}
        # A lifted target moves with the TCP; arm gradients must also survive.
        planner.context['fov_attached_offset']=planner.args.to_device([.02,.01,.03])
        q2=q.detach().clone().requires_grad_(True)
        loss(q2).sum().backward();attached={}
        for name in ('base_theta','torso_1','left_arm_1','left_arm_3'):
            j=fk.joint_names.index(name);step=.001
            plus=q2.detach().clone();minus=q2.detach().clone();plus[0,1,j]+=step;minus[0,1,j]-=step
            finite=(float(loss(plus).sum().item())-float(loss(minus).sum().item()))/(2*step)
            actual=float(q2.grad[0,1,j].item())
            assert np.isclose(actual,finite,rtol=.025,atol=2.),(name,actual,finite)
            attached[name]={'autograd':actual,'finite_difference':finite}
        planner.context['fov_attached_offset']=None
        managers=[]
        for solver in (planner.motion.ik_solver,planner.motion.trajopt_solver):
            assert solver.core.metrics_rollout.metrics_constraint_manager.config.head_fov_endpoint_check
            for rollout in solver.core.optimizer_rollouts:
                assert rollout.cost_manager.head_fk is not None
                assert rollout.constraint_manager.head_fk is None
                managers.append({'rollout':rollout.rollout_instance_name,'soft_fov':True,'constraint_fov':False})
        # Independently compare real MuJoCo camera projection to CUDA FK at
        # torso heights spanning the robot's allowed range; scratch data only.
        optical=[]
        values=planner.current_joints()
        for h in (0.,.123,.369,.615,.738):
            values[-1]=h
            js=JointState.from_position(planner.args.to_device(values).reshape(1,1,-1),joint_names=list(planner.names)).reorder(fk.joint_names)
            cuda=target_camera_torch(fk,js,planner.context).detach().cpu().numpy().reshape(3)
            scratch=np.asarray(planner.fov_validator.project(values)['target_camera'])
            error=float(np.linalg.norm(cuda-scratch));assert error<.001,error
            optical.append({'h':h,'projection_error_m':error})
        write_json(args.output/'summary.json',{'device':str(q.device),'shape':list(terms.shape),
            'gradient_comparisons':comparisons,'attached_gradient_comparisons':attached,'manager_isolation':managers,'optical_height_grid':optical,'passed':True})
        print('PASSED',args.output/'summary.json')
    finally:
        if planner is not None:planner.close()
        sim.close()

if __name__=='__main__':main()
