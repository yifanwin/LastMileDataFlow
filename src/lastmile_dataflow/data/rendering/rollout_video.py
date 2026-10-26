"""Compose three measured camera views and a top-down base trail into one mp4.

All drawn text is ASCII so the renderer does not depend on a CJK font.
"""
from functools import lru_cache
from pathlib import Path

import imageio.v2 as imageio
import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...utils.io import read_json, write_json
from .replay import restore_frame

CAMERAS = ('robot0_agentview_left', 'robot0_eye_in_hand', 'robot0_agentview_right')
LABELS = {
    'robot0_agentview_left': 'left',
    'robot0_eye_in_hand': 'wrist',
    'robot0_agentview_right': 'right',
}
TILE = 256
HEADER = 74
FOOTER = 40
BACKGROUND = '#101820'


@lru_cache(maxsize=8)
def _font(size):
    path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if path.is_file():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _nav_panel(history, station_xy, anchor_xy, supports, radius_m, size=TILE):
    """Local top-down panel: target, station, support, base trail and heading."""
    points = [anchor_xy, *np.asarray(history)[:, :2].tolist()]
    points = np.asarray(points, float)
    center = np.asarray(station_xy, float)
    reach = float(np.max(np.linalg.norm(points - center, axis=1))) if len(points) else 0.
    span = max(reach * 2.6, (radius_m or 0.) * 3.5, 1.0)

    def pixel(xy):
        v = (np.asarray(xy, float) - center) / span + .5
        return (int(v[0] * size), int((1 - v[1]) * size))

    image = Image.new('RGB', (size, size), BACKGROUND)
    draw = ImageDraw.Draw(image)
    step = .5
    for offset in np.arange(center[0] - span / 2, center[0] + span / 2 + step, step):
        x, _ = pixel([offset, center[1]])
        draw.line((x, 0, x, size), fill='#1b2936')
    for offset in np.arange(center[1] - span / 2, center[1] + span / 2 + step, step):
        _, y = pixel([center[0], offset])
        draw.line((0, y, size, y), fill='#1b2936')
    for support in supports:
        x0, y1 = pixel([support['min'][0], support['min'][1]])
        x1, y0 = pixel([support['max'][0], support['max'][1]])
        draw.rectangle((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)),
                       fill='#493d2d', outline='#b99a62')
    if len(history) > 1:
        trail = [pixel(xy) for xy in np.asarray(history)[:, :2]]
        draw.line(trail, fill='#72d8ea', width=3)
    tx, ty = pixel(anchor_xy)
    draw.rectangle((tx - 5, ty - 5, tx + 5, ty + 5), outline='#ffc55b', width=2)
    draw.text((tx + 7, ty - 9), 'T', font=_font(14), fill='#ffc55b')
    sx, sy = pixel(station_xy)
    if radius_m:
        r = radius_m / span * size
        draw.ellipse((sx - r, sy - r, sx + r, sy + r), outline='#66d68c', width=2)
    draw.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill='#66d68c')
    draw.text((sx + 7, sy - 9), 'S1', font=_font(14), fill='#66d68c')
    bx, by = pixel(history[-1][:2])
    draw.ellipse((bx - 4, by - 4, bx + 4, by + 4), fill='white')
    yaw = float(history[-1][2])
    draw.line((bx, by, bx + 16 * np.cos(yaw), by - 16 * np.sin(yaw)),
              fill='white', width=2)
    draw.rectangle((0, 0, size, 20), fill='#102030')
    draw.text((6, 2), 'top-down / base trail', font=_font(13), fill='white')
    scale = .5 if span > .6 else .25
    draw.line((8, size - 12, 8 + scale / span * size, size - 12), fill='white', width=2)
    draw.text((8 + scale / span * size + 4, size - 20),
              f'{scale:g} m', font=_font(12), fill='white')
    return np.asarray(image)


def compose_grid(frames, *, instruction, station_id, phase, time_s, step, steps,
                 base_history, station_xy, anchor_xy, supports=(), base_radius_m=None):
    width, height = TILE * 2, HEADER + TILE * 2 + FOOTER
    canvas = Image.new('RGB', (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 5), instruction[:52], font=_font(17), fill='white')
    draw.text((10, 27), f'{station_id} | phase {phase}', font=_font(14), fill='#72d8ea')
    draw.text((10, 47), f'step {step}/{steps} | {time_s:.2f} s | 1x | no reset',
              font=_font(14), fill='#cbd5e1')
    panels = {
        'robot0_agentview_left': (0, 0),
        'robot0_agentview_right': (TILE, 0),
        'robot0_eye_in_hand': (0, TILE),
    }
    for camera, (left, top) in panels.items():
        tile = Image.fromarray(frames[camera]).resize((TILE, TILE), Image.BILINEAR)
        canvas.paste(tile, (left, HEADER + top))
        draw.rectangle((left, HEADER + top, left + TILE, HEADER + top + 20), fill='#102030')
        draw.text((left + 6, HEADER + top + 2), LABELS[camera], font=_font(14), fill='white')
    nav = _nav_panel(base_history, station_xy, anchor_xy, supports, base_radius_m)
    canvas.paste(Image.fromarray(nav), (TILE, HEADER + TILE))
    draw.text((10, height - 28),
              'measured replay | new_physical_steps=0 | 3 cams + top-down',
              font=_font(12), fill='#cbd5e1')
    return np.asarray(canvas)


def _station_context(metadata):
    preparation = metadata.get('preparation') or {}
    requested = preparation.get('requested_station') or {}
    station_xy = requested.get('xy') or (preparation.get('actual_base') or [0, 0])[:2]
    task = metadata['task']
    scene_config = read_json(Path(metadata['model_dir']) / 'config.json')
    return {
        'station_id': requested.get('station_id', 'S1'),
        'station_xy': np.asarray(station_xy, float),
        'anchor_xy': np.asarray(task['anchor_world'][:2], float),
        'supports': task.get('support_obstacles', []),
        'base_radius_m': (scene_config.get('robot') or {}).get('base_radius_m'),
    }


def render_rollout_grid(rollout, output, *, cameras=CAMERAS, fps=None):
    """Render one measured rollout as a 2x2 camera + top-down mp4 without physics."""
    rollout, output = Path(rollout), Path(output)
    metadata = read_json(rollout / 'recording.json')
    if metadata['steps'] == 0:
        raise ValueError('no executed steps to replay')
    if metadata['mujoco_version'] != mujoco.__version__:
        raise ValueError('replay requires the recorded MuJoCo version')
    if not metadata.get('model_dir'):
        raise ValueError('recording has no stable scene model reference')
    if not metadata['complete'] or not metadata['replay_complete']:
        raise ValueError('recording is incomplete')
    missing = [camera for camera in cameras if camera not in metadata['cameras']]
    if missing:
        raise ValueError(f'recording is missing cameras: {missing}')
    context = _station_context(metadata)
    task = metadata['task']
    title = task.get('instruction', '')
    if not title.isascii():
        title = f"{task.get('asset_id', task.get('category', ''))} | {task.get('operation', '')} | {context['station_id']}"
    width, height = metadata['resolution']
    model = mujoco.MjModel.from_binary_path(str(Path(metadata['model_dir']) / 'model.mjb'))
    data = mujoco.MjData(model)
    output.mkdir(parents=True, exist_ok=False)
    fps = metadata['control_hz'] if fps is None else fps
    if fps <= 0:
        raise ValueError('fps must be positive')
    with np.load(rollout / 'trajectory.npz', allow_pickle=False) as trajectory:
        times = trajectory['time_s'] - trajectory['time_s'][0]
        if len(times) != metadata['steps'] + 1 or not np.all(np.diff(times) > 0):
            raise ValueError('incomplete or non-monotonic measured trajectory')
        playback = np.arange(int(np.ceil(times[-1] * fps - 1e-9)) + 1) / fps
        indices = np.searchsorted(times, playback + 1e-9, side='right') - 1
        phases = trajectory['phases']
        with (mujoco.Renderer(model, height=height, width=width) as renderer,
              imageio.get_writer(output / 'grid.mp4', fps=fps, codec='libx264',
                                 macro_block_size=2) as writer):
            for index in indices:
                restore_frame(model, data, trajectory, index)
                frames = {}
                for camera in cameras:
                    renderer.update_scene(data, camera=metadata['cameras'][camera])
                    frames[camera] = renderer.render().copy()
                phase = phases[index - 1] if index else 'initialize'
                writer.append_data(compose_grid(
                    frames,
                    instruction=title,
                    station_id=context['station_id'],
                    phase=str(phase),
                    time_s=float(times[index]),
                    step=int(index),
                    steps=int(metadata['steps']),
                    base_history=trajectory['base_xyyaw'][:index + 1].copy(),
                    station_xy=context['station_xy'],
                    anchor_xy=context['anchor_xy'],
                    supports=context['supports'],
                    base_radius_m=context['base_radius_m'],
                ))
    result = {
        'source': 'measured_state_replay',
        'rollout': str(rollout.resolve()),
        'layout': 'three_camera_top_down',
        'cameras': list(cameras),
        'fps': fps,
        'frames': len(indices),
        'station_id': context['station_id'],
        'base_radius_m': context['base_radius_m'],
        'physics_stepped': False,
        'new_physical_steps': 0,
        'source_frame_indices': indices.tolist(),
        'playback_times_s': playback.tolist(),
    }
    write_json(output / 'video.json', result)
    return result


def render_run_grid(run, output, *, rollouts, fps=None):
    """Explicit rollout selection; output stays separate from immutable raw results."""
    run, output = Path(run), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for identity in rollouts:
        render_rollout_grid(run / 'rollouts' / identity, output / identity, fps=fps)
    return output
