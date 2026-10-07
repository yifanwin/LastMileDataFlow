"""Actual head RGB/segmentation; observations are reused, never recentered after edits."""
import shutil
from pathlib import Path
import imageio.v2 as imageio
import mujoco
import numpy as np

from ..io import digest, file_digest, write_json
from ..runtime.preparation import preparation_guard
from ..scenes.geometry import descendants
from .edit_views import RenderError, ViewConfig


def camera_state(sim):
    name = sim.robot.camera_names['head_camera']
    camera = sim.model.camera(name).id
    return {'name': name, 'position': sim.data.cam_xpos[camera].tolist(),
            'rotation_matrix': sim.data.cam_xmat[camera].reshape(3, 3).tolist(),
            'fovy_deg': float(sim.model.cam_fovy[camera])}


def head_frame(sim, target, config=None, *, renderer=None):
    preparation_guard(sim)
    config = config or ViewConfig()
    own = renderer is None
    renderer = renderer or mujoco.Renderer(sim.model, height=config.height, width=config.width)
    try:
        renderer.disable_segmentation_rendering()
        renderer.update_scene(sim.data, camera=sim.robot.camera_names['head_camera'])
        rgb = renderer.render().copy()
        renderer.enable_segmentation_rendering()
        segmentation = renderer.render().copy()
        renderer.disable_segmentation_rendering()
        bodies = descendants(sim.model, sim.model.body(target).id)
        ids = np.flatnonzero(np.isin(sim.model.geom_bodyid, list(bodies)))
        mask = (segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & np.isin(segmentation[:, :, 0], ids)
        ys, xs = np.nonzero(mask)
        return rgb, mask, {'target': target, 'visible_pixels': int(mask.sum()), 'image_fraction': float(mask.mean()),
            'bbox_xyxy': [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None,
            'method': 'actual_rendered_geom_segmentation_walls_not_hidden', 'task_feasibility': 'unknown'}
    except (ValueError, KeyError):
        raise
    except Exception as exc:
        raise RenderError('head_render_failed:' + type(exc).__name__) from None
    finally:
        if own:
            renderer.close()


def save_observation(sim, graph, target, path, *, config=None):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    rgb, mask, visibility = head_frame(sim, target, config)
    imageio.imwrite(path/'before_head.png', rgb)
    imageio.imwrite(path/'target_mask.png', mask.astype(np.uint8)*255)
    observation = {'graph_id': graph.to_dict()['graph_id'], 'camera': camera_state(sim),
        'robot_state': sim.robot.state(), 'visibility': visibility,
        'image': {'view': 'before/head', 'path': str((path/'before_head.png').resolve())},
        'image_sha256': file_digest(path/'before_head.png')}
    write_json(path/'observation.json', observation)
    return observation


def render_head_pair(before_sim, after_sim, before_graph, after_graph, observation, path, *,
                     sample_id, target, min_target_pixels=24, config=None):
    preparation_guard(before_sim)
    preparation_guard(after_sim)
    config = config or ViewConfig()
    if observation['graph_id'] != before_graph.to_dict()['graph_id']:
        raise ValueError('stale head observation')
    if file_digest(observation['image']['path']) != observation['image_sha256']:
        raise ValueError('modified before head image')
    if before_sim.robot.state() != observation['robot_state'] or camera_state(before_sim) != observation['camera']:
        raise ValueError('baseline changed since head observation')
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    state = after_sim.robot.state()
    drift = {g: float(np.max(np.abs(np.asarray(state[g])-q))) for g, q in observation['robot_state'].items()}
    camera, baseline_camera = camera_state(after_sim), observation['camera']
    position_drift = float(np.linalg.norm(np.asarray(camera['position'])-baseline_camera['position']))
    rotation_drift = float(np.linalg.norm(np.asarray(camera['rotation_matrix'])-baseline_camera['rotation_matrix']))
    same_initial = all(d <= config.initial_group_tolerance for d in drift.values())
    same_initial &= position_drift <= config.camera_position_tolerance_m and rotation_drift <= config.camera_matrix_tolerance
    same_initial &= camera['name'] == baseline_camera['name'] and camera['fovy_deg'] == baseline_camera['fovy_deg']
    rgb, mask, visibility = head_frame(after_sim, target, config)
    shutil.copyfile(observation['image']['path'], path/'before_head.png')
    imageio.imwrite(path/'after_head.png', rgb)
    imageio.imwrite(path/'after_target_mask.png', mask.astype(np.uint8)*255)
    visible = min(observation['visibility']['visible_pixels'], visibility['visible_pixels']) >= min_target_pixels
    packet = {'sample_id': sample_id, 'before_graph_id': before_graph.to_dict()['graph_id'],
        'after_graph_id': after_graph.to_dict()['graph_id'], 'rig': baseline_camera,
        'framing': 'same_robot_initial_state_and_head_camera', 'not_robot_input': False,
        'before_image_sha256': observation['image_sha256'],
        'visibility': {'before': observation['visibility'], 'after': visibility, 'min_target_pixels': min_target_pixels},
        'initial_state_check': {'pass': bool(same_initial), 'max_abs_group_drift': drift,
                                'camera_position_drift_m': position_drift, 'camera_matrix_drift': rotation_drift,
                                'group_tolerance': config.initial_group_tolerance,
                                'camera_position_tolerance_m': config.camera_position_tolerance_m,
                                'camera_matrix_tolerance': config.camera_matrix_tolerance}, 'valid': bool(same_initial and visible)}
    packet['pair_id'] = digest(packet)
    packet['images'] = [{'view': stage + '/head', 'path': str((path/(stage + '_head.png')).resolve()),
                         'pair_id': packet['pair_id']} for stage in ('before', 'after')]
    write_json(path/'pair.json', packet)
    return packet
