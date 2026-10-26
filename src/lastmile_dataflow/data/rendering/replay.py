"""Offline video from measured whole-scene boundaries; never runs physics."""
from pathlib import Path
import time

import imageio.v2 as imageio
import mujoco
import numpy as np

from ...utils.io import read_json, write_json


def restore_frame(model, data, trajectory, index):
    mujoco.mj_setState(model, data, trajectory['integration_state'][index],
                       int(trajectory['state_spec']))
    mujoco.mj_forward(model, data)
    # A live camera may have moved independently of its mount.
    data.cam_xpos[:] = trajectory['camera_xpos'][index]
    data.cam_xmat[:] = trajectory['camera_xmat'][index]
    model.cam_fovy[:] = trajectory['camera_fovy'][index]
    model.cam_intrinsic[:] = trajectory['camera_intrinsic'][index]
    model.cam_sensorsize[:] = trajectory['camera_sensorsize'][index]


def render_rollout(rollout, output, *, camera, fps=None):
    """Sample measured timestamps at output FPS, holding the preceding boundary."""
    started = time.monotonic()
    rollout, output = Path(rollout), Path(output)
    metadata = read_json(rollout / 'recording.json')
    if metadata['steps'] == 0:
        raise ValueError('no executed steps to replay')
    if metadata['mujoco_version'] != mujoco.__version__:
        raise ValueError('replay requires the recorded MuJoCo version')
    if not metadata.get('model_dir'):
        raise ValueError('recording has no stable scene model reference')
    if not metadata['complete']:
        raise ValueError('recording is incomplete')
    fps = metadata['control_hz'] if fps is None else fps
    if fps <= 0:
        raise ValueError('fps must be positive')
    camera_name = metadata['cameras'][camera]
    width, height = metadata['resolution']
    model = mujoco.MjModel.from_binary_path(str(Path(metadata['model_dir']) / 'model.mjb'))
    data = mujoco.MjData(model)
    output.mkdir(parents=True, exist_ok=False)
    with np.load(rollout / 'trajectory.npz', allow_pickle=False) as trajectory:
        times = trajectory['time_s'] - trajectory['time_s'][0]
        if len(times) != metadata['steps'] + 1 or not np.all(np.diff(times) > 0):
            raise ValueError('incomplete or non-monotonic measured trajectory')
        playback = np.arange(int(np.ceil(times[-1] * fps - 1e-9)) + 1) / fps
        indices = np.searchsorted(times, playback + 1e-9, side='right') - 1
        with (mujoco.Renderer(model, height=height, width=width) as renderer,
              imageio.get_writer(output / f'{camera}.mp4', fps=fps,
                                 codec='libx264', macro_block_size=2) as writer):
            for index in indices:
                restore_frame(model, data, trajectory, index)
                renderer.update_scene(data, camera=camera_name)
                writer.append_data(renderer.render())
    result = {'source': 'measured_state_replay', 'rollout': str(rollout.resolve()),
              'camera': camera, 'fps': fps, 'frames': len(indices),
              'measured_times_s': times.tolist(), 'source_frame_indices': indices.tolist(),
              'playback_times_s': playback.tolist(), 'elapsed_s': time.monotonic() - started}
    write_json(output / 'replay.json', result)
    return result


def render_run(run, output, *, rollouts, camera):
    """Explicit rollout selection; output is separate from immutable raw results."""
    run, output = Path(run), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for identity in rollouts:
        render_rollout(run / 'rollouts' / identity, output / identity, camera=camera)
    return output


def render_observations(rollout, output, *, cameras, depth=False):
    """Render every recorded boundary for training, with no physics or interpolation."""
    rollout, output = Path(rollout), Path(output)
    metadata = read_json(rollout / 'recording.json')
    if not metadata['replay_complete'] or metadata['steps'] <= 0 or metadata['mujoco_version'] != mujoco.__version__:
        raise ValueError('training replay requires a complete executed trace and matching MuJoCo')
    model = mujoco.MjModel.from_binary_path(str(Path(metadata['model_dir']) / 'model.mjb'))
    data = mujoco.MjData(model)
    output.mkdir(parents=True, exist_ok=False)
    width, height = metadata['resolution']
    started = time.monotonic()
    for camera in cameras:
        (output / 'images' / camera).mkdir(parents=True)
    with (np.load(rollout / 'trajectory.npz', allow_pickle=False) as trajectory,
          mujoco.Renderer(model, height=height, width=width) as renderer):
        if len(trajectory['integration_state']) != metadata['steps'] + 1:
            raise ValueError('training replay has an incomplete measured state sequence')
        for index in range(metadata['steps'] + 1):
            restore_frame(model, data, trajectory, index)
            for camera in cameras:
                renderer.update_scene(data, camera=metadata['cameras'][camera])
                imageio.imwrite(output / 'images' / camera / f'{index:06d}.png', renderer.render())
                if depth:
                    renderer.enable_depth_rendering()
                    values = renderer.render().copy()
                    renderer.disable_depth_rendering()
                    np.save(output / 'images' / camera / f'{index:06d}-depth.npy', values)
    result = {'complete': True, 'source': 'measured_state_replay', 'rollout': str(rollout.resolve()),
              'cameras': list(cameras), 'record_depth': depth, 'frames': metadata['steps'] + 1,
              'elapsed_s': time.monotonic() - started, 'new_physical_steps': 0}
    write_json(output / 'observations.json', result)
    return result
