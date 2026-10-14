"""Actual head RGB/segmentation; observations are reused, never recentered after edits."""
import shutil
from pathlib import Path
import imageio.v2 as imageio
import mujoco
import numpy as np

from ..io import write_json
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
