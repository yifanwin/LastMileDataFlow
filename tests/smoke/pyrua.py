"""Real P1/P2 checks: RPent RoboCasa primitives on one project benchmark episode.

Runs the same chain as `evaluate-bench` with the pyrua backend (one Simulation,
AttemptRecorder, project evidence, RPent toolkit over ProjectEnvClient, RLDX-1
server) but a fixed primitive script replaces the agent. It reports measured
responses: world-map accuracy at the target, base motion direction against the
heading, arm deltas, gripper opening and one short RLDX-1 call. Ground-truth
poses are read here only for the measurement, never by the primitives.
`--agent` (P2) runs the real PyRUA agent on the episode instead of the script.

    MUJOCO_GL=egl .venv/bin/python tests/smoke/pyrua.py --benchmark <bench> --episode e000000 \\
        --output outputs/pyrua-smoke/<run> --gpu <index>
"""
import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sys
import time

import numpy as np


def script(robo, environment):
    sim = environment.sim
    owner = environment.owner
    report = {}
    state = robo.state()
    report['initial'] = {'base_pos': state.base_pos, 'base_yaw': state.base_yaw, 'eef_pos': state.eef_pos,
                         'gripper_opening': state.gripper_opening, 'task': state.task,
                         'task_progress': state.task_progress}

    def target_pixel():
        calibration = environment.sensor('robot0_agentview_left', 256, 256)[2]
        point = sim.data.xpos[sim.target_id].copy()
        transform = np.asarray(calibration['T_world_camera_optical'])
        local = transform[:3, :3].T @ (point - transform[:3, 3])
        (fx, _, cx), (_, fy, cy), _ = calibration['intrinsics']['K']
        return point.tolist(), [int(round(fy*local[1]/local[2]+cy)), int(round(fx*local[0]/local[2]+cx))]

    def ray_check():
        # Compare world-map points with MuJoCo ray casts through the same pixels.
        import mujoco
        calibration = environment.sensor('robot0_agentview_left', 256, 256)[2]
        world = environment.world_map('robot0_agentview_left', 256, 256)
        transform = np.asarray(calibration['T_world_camera_optical'])
        (fx, _, cx), (_, fy, cy), _ = calibration['intrinsics']['K']
        errors = []
        geomid = np.zeros(1, np.int32)
        for row in range(16, 256, 32):
            for col in range(16, 256, 32):
                direction = transform[:3, :3] @ np.array([(col-cx)/fx, (row-cy)/fy, 1.])
                direction /= np.linalg.norm(direction)
                # Visible geometry only: the renderer shows groups 0-2 of non-robot geoms.
                distance = mujoco.mj_ray(sim.model, sim.data, transform[:3, 3], direction,
                                         np.array([1, 1, 1, 0, 0, 0], np.uint8), 1, -1, geomid)
                if distance > 0 and np.isfinite(world[row, col]).all():
                    hit = transform[:3, 3] + distance*direction
                    errors.append(float(np.linalg.norm(hit - world[row, col])))
        return {'pixels': len(errors), 'median_m': float(np.median(errors)), 'p90_m': float(np.percentile(errors, 90)),
                'max_m': float(np.max(errors))}

    report['world_map_ray_check'] = owner.call(ray_check)
    point, pixel = owner.call(target_pixel)
    projection = robo.back_project_batch([pixel])
    xyz = projection.points[0].world_xyz if projection.points[0].valid else None
    report['world_map'] = {'target_center_gt': point, 'pixel': pixel, 'back_projected': xyz,
                           'distance_m': None if xyz is None else float(np.linalg.norm(np.subtract(xyz, point)))}

    def base(**kwargs):
        robo.move_base(steps=20)   # zero command: let the previous velocity settle
        before = robo.state()
        result = robo.move_base(steps=10, **kwargs)
        after = robo.state()
        move = np.subtract(after.base_pos, before.base_pos)[:2]
        return {'command': kwargs, 'moved_m': float(np.linalg.norm(move)),
                'direction_minus_heading_deg': float(math.degrees((math.atan2(move[1], move[0]) - before.base_yaw
                                                                   + math.pi) % (2*math.pi) - math.pi)),
                'yaw_change_deg': float(math.degrees(after.base_yaw - before.base_yaw)),
                'steps': result.steps}

    report['base'] = [base(forward=.5), base(forward=-.5), base(lateral=.5), base(turn=.5), base(turn=-.5)]

    def arm(dxyz):
        before = robo.state()
        result = robo.move_delta(dxyz)
        after = robo.state()
        return {'command': list(dxyz), 'reached': result.reached, 'steps': result.steps_used,
                'eef_delta': np.subtract(after.eef_pos, before.eef_pos).round(4).tolist()}

    report['arm'] = [arm((0, 0, .05)), arm((.05, 0, 0)), arm((0, .05, 0)), arm((0, 0, -.05))]
    closed = robo.set_gripper(1., 15)
    opened = robo.release(15)
    report['gripper'] = {'closed_opening': closed.gripper_opening, 'opened_opening': opened.gripper_opening}
    if 'rldx_arm' not in robo._hidden:
        started = time.monotonic()
        skill = robo.rldx_arm(max_chunks=3)
        report['rldx'] = {**asdict(skill), 'wall_s': time.monotonic()-started}
    report['final_progress'] = robo.state().task_progress
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--benchmark', type=Path, required=True)
    parser.add_argument('--episode', default='e000000')
    parser.add_argument('--policy-config', type=Path, default=Path('configs/policies/pyrua_panda_omron.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--agent', action='store_true', help='P2: the real PyRUA agent instead of the fixed script')
    args = parser.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu))
    os.environ.setdefault('MUJOCO_GL', 'egl')

    from lastmile_dataflow.config import CollectionConfig, robot_config
    from lastmile_dataflow.policies.pyrua.client import PyruaPolicy
    from lastmile_dataflow.runtime.execution.agent import run_agent_attempt
    from lastmile_dataflow.runtime.execution_config import ExecutionConfig
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.utils.io import create_run, read_json, rows

    class ScriptedPrimitives(PyruaPolicy):
        def run(self, environment, out_dir, *, seed):
            import threading
            from lastmile_dataflow.policies.pyrua import host
            out_dir.mkdir(parents=True)
            box = {}

            def target():
                try:
                    if self.vla is not None:
                        self.vla.client.reset_session()
                    toolkit, _ = host.boot(None, out_dir, environment=environment,
                                           vla_client=None if self.vla is None else self.vla.client)
                    robo = host.LastMilePandaRobot(host.make_backend(toolkit), primitives=self.config.primitives)
                    box['report'] = script(robo, environment)
                    toolkit.close()
                except BaseException as exc:
                    box['error'] = exc

            worker = threading.Thread(target=target, daemon=True)
            worker.start()
            environment.owner.serve(worker)
            if 'error' in box:
                raise box['error']
            (out_dir/'probe.json').write_text(json.dumps(box['report'], indent=2, default=float)+'\n')
            return {'status': 'completed', 'finish': None, 'probe': box['report']}

    benchmark = args.benchmark.resolve()
    episode = next(e for e in rows(benchmark/'episodes.jsonl') if e['episode_id'] == args.episode)
    saved = read_json(benchmark/'config.json')
    root = create_run(args.output, {'benchmark': str(benchmark), 'episode': args.episode,
                                    'kind': 'pyrua-p2-agent' if args.agent else 'pyrua-p1-smoke'})
    policy = (PyruaPolicy if args.agent else ScriptedPrimitives)(args.policy_config, root/'policy.log')
    try:
        snapshot = benchmark/episode['scene']
        robot = robot_config(read_json(snapshot/'config.json')['robot'])
        sim = Simulation.from_snapshot(snapshot, robot, target=episode['task']['target_body'])
        reference = saved['reference_execution']
        config = ExecutionConfig(operation_backend='pyrua', operation_base_mode='policy_relative',
                                 policy_config=str(args.policy_config), width=reference['width'],
                                 height=reference['height'], radius_m=reference['radius_m'],
                                 attempt_timeout_s=policy.config.attempt_timeout_s, third_person_enabled=True)
        collection = CollectionConfig(output_dir=str(root), width=config.width, height=config.height,
                                      seed=episode['seed'], max_steps=policy.config.control_step_budget,
                                      record_video=True, record_images=False,
                                      video_cameras={robot.observation_camera: 'observation',
                                                     'third_person_camera': 'third_person'})
        started = time.monotonic()
        result = run_agent_attempt(sim, episode['task'], episode['station'], config, collection, root=root,
                                   attempt_id=args.episode, seed=episode['seed'], policy=policy)
        result['wall_s'] = time.monotonic()-started
        sim.close()
    finally:
        policy.close()
    summary = {key: result.get(key) for key in ('status', 'reason', 'executed_steps', 'control_steps',
                                                'recording_complete', 'execution_termination', 'task_outcome',
                                                'elapsed_wall_s', 'exception_detail')}
    agent = result.get('agent') or {}
    summary['probe'] = agent.get('probe')
    if args.agent:
        summary['agent'] = {key: agent.get(key) for key in ('status', 'reason', 'finish', 'tool_turns', 'calls',
                                                           'stateful_calls', 'env_steps', 'policy_wall_s',
                                                           'startup_s', 'total_wall_s', 'notes', 'error')}
        summary['agent']['generation'] = {key: (agent.get('generation') or {}).get(key) for key in (
            'turns', 'tool_calls', 'input_tokens', 'cached_input_tokens', 'output_tokens',
            'reasoning_output_tokens', 'total_tokens', 'errors', 'compactions')}
    (root/'smoke.json').write_text(json.dumps(summary, indent=2, default=float)+'\n')
    json.dump(summary, sys.stdout, indent=2, default=float)
    print()


if __name__ == '__main__':
    main()
