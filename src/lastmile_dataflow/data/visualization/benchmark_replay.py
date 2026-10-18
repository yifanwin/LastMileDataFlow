"""Add two videos to recorded evaluations without rerunning policy or physics.

Usage: python -m lastmile_dataflow.data.visualization.benchmark_replay <evaluation> --gpu 3
"""
import argparse
import os
from pathlib import Path

import imageio.v2 as imageio
import numpy as np

from ...config import robot_config
from ...utils.io import read_json, rows, write_json
from .benchmark import evaluation_view


def reconstruct(root):
    os.environ.setdefault("MUJOCO_GL", "egl")
    import mujoco
    from ...runtime.simulation import Simulation

    root = Path(root).resolve()
    config = read_json(root / 'config.json')
    benchmark = Path(config['benchmark'])
    episodes = {e['episode_id']: e for e in rows(benchmark / 'episodes.jsonl')}
    for result in rows(root / 'results.jsonl'):
        eid = result['episode_id']
        rollout = root / 'rollouts' / (result.get('rollout_id') or eid)
        if not (rollout / 'trajectory.npz').exists():
            continue
        episode = episodes[eid]
        snapshot = benchmark / episode['scene']
        saved = read_json(snapshot / 'config.json')
        profile = config['scene_profiles'][episode['scene_id']]
        # Rendering uses the frozen geometry and recorded state, with the run's
        # camera settings. Actuator gains are irrelevant: no physics is stepped.
        model = mujoco.MjModel.from_binary_path(str(snapshot / 'model.mjb'))
        sim = Simulation(model, robot_config(profile), saved['instances'], episode['task']['target_body'])
        if profile['control_profile'] == 'molmobot-native-v1':
            for alias, fovy in (('head_camera', 139.), ('wrist_camera_l', 58.), ('wrist_camera_r', 58.)):
                model.cam_fovy[model.camera(profile['namespace'] + alias).id] = fovy
        width, height = read_json(rollout / 'camera_profile.json')['resolution']
        model.vis.global_.offwidth = max(model.vis.global_.offwidth, width)
        model.vis.global_.offheight = max(model.vis.global_.offheight, height)
        camera = sim.enable_third_person()
        directory = rollout / 'videos'
        directory.mkdir(exist_ok=True)
        if any((directory / f'{k}.mp4').exists() for k in ('head', 'third_person')):
            sim.close()
            raise FileExistsError(f'videos already exist: {directory}')
        writers = {}
        try:
            with np.load(rollout / 'trajectory.npz', allow_pickle=False) as trace:
                if trace['qpos'].shape[1] != model.nq or trace['qvel'].shape[1] != model.nv:
                    raise ValueError('recorded state does not match frozen geometry')
                times = (trace['time_s'] - trace['time_s'][0]).tolist()
                fps = profile['control_hz']
                for name in ('head', 'third_person'):
                    writers[name] = imageio.get_writer(directory / f'{name}.mp4', fps=fps,
                        codec='libx264', macro_block_size=2)
                for i in range(len(times)):
                    sim.data.qpos[:] = trace['qpos'][i]
                    sim.data.qvel[:] = trace['qvel'][i]
                    sim.data.ctrl[:] = trace['ctrl'][i]
                    sim.data.time = trace['time_s'][i]
                    mujoco.mj_forward(model, sim.data)
                    if sim.renderer is None:
                        sim.renderer = mujoco.Renderer(model, width=width, height=height)
                    third = camera.render(sim, sim.renderer, width, height)
                    head_path = rollout / 'images' / 'head_camera' / f'{i:06d}.png'
                    if not head_path.exists():
                        raise FileNotFoundError(f'original head frame missing: {head_path}')
                    writers['head'].append_data(imageio.imread(head_path))
                    writers['third_person'].append_data(third)
                    if i % 50 == 0:
                        print(f'{eid}: rendered {i + 1}/{len(times)}', flush=True)
                metadata = camera.metadata()
        finally:
            for writer in writers.values():
                writer.close()
            sim.close()
        recording = read_json(rollout / 'recording.json')
        recording['videos'] = {name: {'path': f'videos/{name}.mp4', 'frames': len(times),
            'fps': fps, 'sample_times_s': times,
            'source': 'recorded_images' if name == 'head' else 'recorded_state_reconstruction'}
            for name in ('head', 'third_person')}
        write_json(rollout / 'recording.json', recording)
        write_json(rollout / 'third_person_reconstruction.json', {**metadata,
            'source': 'trajectory.npz qpos/qvel/ctrl + benchmark frozen geometry',
            'physics_stepped': False, 'policy_rerun': False})
        print(f'{eid}: wrote head + third_person videos', flush=True)
    return evaluation_view(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evaluation', type=Path)
    parser.add_argument('--gpu', type=int, required=True)
    args = parser.parse_args()
    os.environ.update(MUJOCO_GL='egl', MUJOCO_EGL_DEVICE_ID=str(args.gpu))
    print(reconstruct(args.evaluation))
