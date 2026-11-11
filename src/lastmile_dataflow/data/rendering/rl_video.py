"""Render recorded RL states without re-running control or task scoring."""
from pathlib import Path
import time

from ..recorder import video_writer
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
    target = np.asarray(settings['lookat'])
    base = np.asarray(settings['base_position'])
    azimuth = np.degrees(np.arctan2(base[1] - target[1], base[0] - target[0])) + 30
    for index, (distance, elevation) in enumerate(((2.3, -25), (.45, -20))):
        camera = mujoco.MjvCamera()
        camera.distance, camera.azimuth, camera.elevation = distance, azimuth, elevation
        camera.lookat[:] = (target + base) / 2 if index == 0 else target
        cameras.append(camera)
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    with mujoco.Renderer(model, height=height, width=width) as renderer, video_writer(path, round(1 / dt)) as writer:
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
            'simulation_duration_s': (len(states) - 1) * dt,
            'render_seconds': time.perf_counter() - started}
