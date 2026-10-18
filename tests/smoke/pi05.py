"""Explicit real-checkpoint inference or Panda control; never an offline unit test."""
import argparse
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('model','observation','control','tracking','rollout'), required=True)
    parser.add_argument('--policy-config', default='configs/policies/pi05_panda_omron.json')
    parser.add_argument('--robot-config', default='configs/robots/panda_omron_robocasa.json')
    parser.add_argument('--case', default='configs/execution/panda_omron_fixed_pick.json')
    parser.add_argument('--horizon', type=int, default=100)
    parser.add_argument('--normalized', action='store_true', help='Model-only smoke; no embodiment normalization or task claim')
    parser.add_argument('--observation', type=Path, help='NPZ with physical state, three model image keys and prompt')
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    args.output.mkdir(parents=True,exist_ok=False)
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu),MUJOCO_EGL_DEVICE_ID=str(args.gpu),MUJOCO_GL='egl',
                      XLA_PYTHON_CLIENT_PREALLOCATE='false')
    import numpy as np
    if args.stage == 'model':
        from lastmile_dataflow.policies.pi05.config import load_config
        from lastmile_dataflow.policies.pi05.inference import Pi05Inference,IMAGE_KEYS
        config = load_config(args.policy_config)
        started = time.monotonic()
        runner = Pi05Inference(config, require_stats=not args.normalized)
        load_s = time.monotonic()-started
        runner.reset(args.seed)
        obs = {'state':np.zeros(config.state_size,np.float32),
               'images':{name:np.zeros((224,224,3),np.uint8) for name in IMAGE_KEYS},'prompt':'Pick up the cup.'}
        if args.observation:
            with np.load(args.observation, allow_pickle=False) as data:
                obs = {'state':data['state'], 'images':{name:data[name] for name in IMAGE_KEYS},
                       'prompt':str(data['prompt'])}
        result = runner.infer(obs,normalized=args.normalized)
        np.savez_compressed(args.output/'prediction.npz',actions=result['actions'],raw_actions=result['raw_actions'])
        packet = {'stage':'model_only','synthetic_observation':args.observation is None,
                  'observation_source':str(args.observation) if args.observation else None,'normalized_smoke':args.normalized,
                  'config':asdict(config),'seed':args.seed,'load_s':load_s,'inference_s':result['inference_s'],
                  'action_shape':list(result['actions'].shape),'task_success':None}
    elif args.stage == 'rollout':
        from copy import deepcopy
        from dataclasses import replace
        from lastmile_dataflow.config import load_robot_config, read_config, CollectionConfig
        from lastmile_dataflow.runtime.simulation import Simulation
        from lastmile_dataflow.runtime.execution_config import ExecutionConfig
        from lastmile_dataflow.runtime.execution.vla import run_vla_attempt
        from lastmile_dataflow.scenes.loading.source import SceneSource
        from lastmile_dataflow.scenes.tasks.discovery import discover_tasks
        from lastmile_dataflow.policies.pi05.client import Pi05Client
        config = load_robot_config(args.robot_config)
        case = read_config(args.case, data_fields=('scene_xml','metadata','assets_dir'))
        initial = deepcopy(config.initial)
        if config.name == 'panda_omron':
            initial.update(base_xyyaw=case['base_xyyaw'], lift=case['lift'],
                           gripper_width={'right':case['gripper_width']})
        else:
            initial['base'] = case['base_xyyaw']
        config = replace(config, initial=initial)
        sim = Simulation.from_source(SceneSource('val103-policy-smoke',case['scene_xml'],case['metadata']),config)
        policy = None
        try:
            width,height = (256,256) if config.name=='panda_omron' else (320,240)
            execution = ExecutionConfig(operation_backend='pi05', policy_config=args.policy_config,
                operation_base_mode='policy_relative',head_fov_enabled=False,width=width,height=height,
                gripper_width_m=.11,attempt_timeout_s=300.)
            tasks,_ = discover_tasks(sim,json.loads(Path(case['metadata']).read_text()),execution)
            task = next(t for t in tasks if t['operation']=='pick' and t['asset_id']==case['target_asset']
                        and t['source_object_id']==case['source_object_id'])
            policy = Pi05Client(args.policy_config,args.output/'model.log')
            policy.config = replace(policy.config,task_horizon=args.horizon)
            packet = run_vla_attempt(sim,task,{'station_id':'fixed-smoke','initial':initial,
                'xy':case['base_xyyaw'][:2]},execution,
                CollectionConfig(record_physics=True),root=args.output,attempt_id='policy',seed=args.seed,policy=policy)
            packet.update(stage='real_policy_rollout',uncalibrated_fixed_station=True)
        finally:
            if policy is not None: policy.close()
            sim.close()
    else:
        from lastmile_dataflow.config import load_robot_config
        from lastmile_dataflow.runtime.simulation import Simulation
        from lastmile_dataflow.scenes.loading.source import SceneSource
        path=args.output/'floor.xml'
        path.write_text('<mujoco><worldbody><light pos="0 0 3"/><geom name="floor" type="plane" size="5 5 .01"/></worldbody></mujoco>')
        sim=Simulation.from_source(SceneSource('pi05-control',str(path.resolve())),load_robot_config(args.robot_config))
        try:
            if args.stage=='observation':
                import imageio.v2 as imageio
                from lastmile_dataflow.policies.pi05.observation import observation
                frames=sim.render(256,256)
                for name,pixels in frames.items():
                    imageio.imwrite(args.output/(name+'.png'),pixels)
                obs=observation(sim,frames,'Pick up the cup.')
                np.savez_compressed(args.output/'observation.npz',state=obs['state'],**obs['images'])
                packet={'stage':'real_robot_observation','state':obs['state'].tolist(),'task_success':None}
            elif args.stage == 'tracking':
                from lastmile_dataflow.runtime.control.interface import RobotCommand
                from lastmile_dataflow.runtime.execution.context import semantic_tick
                from lastmile_dataflow.scenes.geometry import tcp_pose
                from lastmile_dataflow.runtime.outcomes.pick import rotation_error
                goal = sim.robot.base_pose() + np.array([.15,.08,.2])
                command = RobotCommand(base_xyyaw=goal,arm_q={'right':sim.robot.arm_positions('right').copy()})
                sim.robot.execution_action(command)
                site = sim.robot.tcp_site('right')
                target_pos = sim.robot.scratch.site_xpos[site].copy()
                target_rot = sim.robot.scratch.site_xmat[site].reshape(3,3).copy()
                sim.begin()
                for _ in range(100): semantic_tick(sim,command)
                measured = tcp_pose(sim,'right')
                packet={'stage':'mobile_reference_tracking','base_error':(sim.robot.base_pose()-goal).tolist(),
                        'tcp_position_error_m':float(np.linalg.norm(measured[:3,3]-target_pos)),
                        'tcp_rotation_error_rad':rotation_error(measured[:3,:3],target_rot),
                        'time_s':float(sim.data.time),'task_success':None}
            else:
                from lastmile_dataflow.runtime.control.interface import RobotCommand
                initial=sim.robot.arm_positions('right').copy()
                site=sim.robot.tcp_site('right');position=sim.data.site_xpos[site].copy()
                sim.begin()
                action=np.r_[np.zeros(6),0.,np.zeros(4),1.]
                for _ in range(40):sim.step(action)
                drift=float(np.max(np.abs(initial-sim.robot.arm_positions('right'))))
                action[0]=.2;sim.step(action);action[0]=0.
                for _ in range(20):sim.step(action)
                packet={'stage':'real_control','hold_arm_drift_rad':drift,
                        'tcp_delta_m':(sim.data.site_xpos[site]-position).tolist(),
                        'time_s':float(sim.data.time),'finite':bool(np.isfinite(sim.data.qpos).all()),'task_success':None}
        finally:
            sim.close()
    (args.output/'result.json').write_text(json.dumps(packet,indent=2)+'\n')
    print(json.dumps(packet,indent=2),flush=True)


if __name__=='__main__':
    main()
