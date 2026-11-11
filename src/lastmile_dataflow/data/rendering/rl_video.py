"""Render recorded RL states without re-running control or task scoring."""
from pathlib import Path
import time

import imageio.v2 as imageio
import mujoco
import numpy as np
from PIL import Image, ImageDraw


def render_rollout(model, states, metrics, path, label, result, settings, dt):
    started = time.perf_counter()
    data = mujoco.MjData(model)
    width, height = settings['width'], settings['height']
    model.vis.global_.offwidth = max(model.vis.global_.offwidth, width)
    model.vis.global_.offheight = max(model.vis.global_.offheight, height)
    cameras = []
    for distance, elevation in ((2.6, -25), (.45, -20)):
        camera = mujoco.MjvCamera()
        camera.distance, camera.azimuth, camera.elevation = distance, 135, elevation
        camera.lookat[:] = settings['lookat']
        cameras.append(camera)
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    with mujoco.Renderer(model, height=height, width=width) as renderer, imageio.get_writer(
            path, fps=round(1 / dt), codec='libx264', macro_block_size=2) as writer:
        for qpos, row in zip(states, metrics):
            data.qpos[:] = qpos
            mujoco.mj_forward(model, data)
            frames = []
            for camera in cameras:
                renderer.update_scene(data, camera=camera)
                frames.append(renderer.render().copy())
            picture = Image.fromarray(np.concatenate(frames, axis=1))
            draw = ImageDraw.Draw(picture)
            text = label + '\n' + row + '\n' + result
            draw.rectangle((0, 0, picture.width, 65), fill='black')
            draw.text((8, 5), text, fill='white')
            writer.append_data(np.asarray(picture))
    return {'path': str(path), 'frames': len(states), 'duration_s': len(states) * dt,
            'render_seconds': time.perf_counter() - started}
