"""Render recorded RL states without re-running control or task scoring."""
from pathlib import Path
import time
from types import SimpleNamespace

from .third_person import ThirdPersonCamera

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
    observer = ThirdPersonCamera()
    target_id = model.body(settings['target_body']).id
    sim = SimpleNamespace(model=model, data=data, target_id=target_id,
                          robot=SimpleNamespace(config=SimpleNamespace(namespace='robot/robot_0/')))
    wrist = 'robot/robot_0/robot0_eye_in_hand'
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    with mujoco.Renderer(model, height=height, width=width) as renderer, video_writer(path, round(1 / dt)) as writer:
        for qpos, row in zip(states, metrics):
            data.qpos[:] = qpos
            mujoco.mj_forward(model, data)
            overview = observer.render(sim, renderer, width, height)
            renderer.update_scene(data, camera=wrist)
            frames = (overview, renderer.render().copy())
            picture = Image.fromarray(np.concatenate(frames, axis=1))
            draw = ImageDraw.Draw(picture)
            text = label + '\n' + row + '\n' + result
            draw.rectangle((0, 0, picture.width, 65), fill='black')
            draw.text((8, 5), text, fill='white')
            writer.append_data(np.asarray(picture))
    return {'path': str(path), 'frames': len(states), 'duration_s': len(states) * dt,
            'camera': {'overview': observer.metadata(), 'close': wrist},
            'simulation_duration_s': (len(states) - 1) * dt,
            'render_seconds': time.perf_counter() - started}
