"""Actual head RGB/segmentation; observations are reused, never recentered after edits."""

import copy
import mujoco
import numpy as np

from ...runtime.construction.preparation import preparation_guard
from ...scenes.geometry import geom_points
from .cameras import ViewConfig, body_geoms, geom_mask


def roi_geoms(sim, task):
    """Open visibility refers to a handle, never the whole articulated cabinet."""
    body = task["target_body"]
    if task["operation"] == "open":
        body = task.get("handle_body")
        if not body or body == task["target_body"] and not task.get("explicit_handle"):
            return []
    return body_geoms(sim.model, body).tolist()


def aggregate_visibility(cameras):
    known = [row["fraction"] for row in cameras.values() if row.get("fraction") is not None]
    complete = bool(cameras) and len(known) == len(cameras)
    return {
        "cameras": cameras,
        "complete": complete,
        "value": max(known) if complete else None,
        "known_max": max(known) if known else None,
        "status": "measured" if complete else "partial" if known else "unknown",
        "aggregation": "max_over_cameras; low requires every camera known",
    }


def _outside_frustum(sim, camera, points, width, height):
    local = (points - sim.data.cam_xpos[camera]) @ sim.data.cam_xmat[camera].reshape(3, 3)
    depth = -local[:, 2]
    tangent = np.tan(np.deg2rad(sim.model.cam_fovy[camera]) / 2)
    near = sim.model.vis.map.znear * sim.model.stat.extent
    return bool(
        np.all(depth <= near)
        or np.all(local[:, 0] > depth * tangent * width / height)
        or np.all(local[:, 0] < -depth * tangent * width / height)
        or np.all(local[:, 1] > depth * tangent)
        or np.all(local[:, 1] < -depth * tangent)
    )


class TaskVisibility:
    """Same-pose ROI segmentation and an unobstructed temporary visual scene.

    Only renderer.scene is edited. Physics model/data and policy images remain
    untouched, and update_scene recreates the observed scene before each view.
    """

    def __init__(self, sim, task, config):
        preparation_guard(sim)
        self.sim, self.task, self.config = sim, task, config
        self.roi = roi_geoms(sim, task)
        # Multisample resolves can blend RGB-encoded segmentation IDs. Disable
        # MSAA only on a render-model copy; never modify the physical model or
        # the settings used for policy observations.
        self.render_model = copy.copy(sim.model)
        self.render_model.vis.quality.offsamples = 0
        self.renderer = mujoco.Renderer(self.render_model, height=config.height, width=config.width)

    def _camera(self, sim, name):
        cfg, renderer = self.config, self.renderer
        camera = sim.model.camera(sim.robot.camera_names[name]).id
        renderer.update_scene(sim.data, camera=camera)
        observed = int(geom_mask(renderer, self.roi).sum())
        roi = [
            copy.copy(g)
            for g in renderer.scene.geoms[: renderer.scene.ngeom]
            if g.objtype == int(mujoco.mjtObj.mjOBJ_GEOM) and g.objid in self.roi
        ]
        # Compact only the temporary MjvScene. Copy all visual fields, including
        # mesh/material bindings, so the reference uses the exact same optics.
        fields = (
            "type",
            "dataid",
            "objtype",
            "objid",
            "category",
            "matid",
            "texcoord",
            "size",
            "pos",
            "mat",
            "rgba",
            "emission",
            "specular",
            "shininess",
            "reflectance",
            "modelrbound",
            "transparent",
        )
        for index, source in enumerate(roi):
            destination = renderer.scene.geoms[index]
            for field in fields:
                value = getattr(source, field)
                if isinstance(value, np.ndarray):
                    getattr(destination, field)[:] = value
                else:
                    setattr(destination, field, value)
            destination.segid = index
        renderer.scene.ngeom = len(roi)
        reference = int(geom_mask(renderer, self.roi).sum())
        points = np.concatenate([geom_points(sim, g) for g in self.roi])
        outside = _outside_frustum(sim, camera, points, cfg.width, cfg.height)
        fraction = None
        if reference >= cfg.min_reference_pixels:
            fraction = min(1.0, observed / reference)
            status = "measured"
        elif outside:
            fraction, status = 0.0, "out_of_view"
        else:
            status = "insufficient_projection"
        return {
            "status": status,
            "fraction": fraction,
            "visible_pixels": observed,
            "reference_pixels": reference,
            "min_reference_pixels": cfg.min_reference_pixels,
            "camera_position_world": sim.data.cam_xpos[camera].tolist(),
            "camera_rotation_world": sim.data.cam_xmat[camera].reshape(3, 3).tolist(),
        }

    def measure(self, sim=None):
        sim = sim or self.sim
        if sim.model is not self.sim.model:
            raise ValueError("visibility renderer belongs to a different model")
        preparation_guard(sim)
        cameras = {}
        for name in sim.robot.config.cameras:
            if not self.roi:
                cameras[name] = {"status": "interaction_roi_not_defined", "fraction": None}
                continue
            try:
                cameras[name] = self._camera(sim, name)
            except (mujoco.FatalError, RuntimeError, IndexError) as exc:
                cameras[name] = {"status": "unknown", "fraction": None, "reason": type(exc).__name__}
        return {
            **aggregate_visibility(cameras),
            "roi_geoms": self.roi,
            "segmentation_msaa_samples": 0,
            "method": "same_pose_roi_segmentation_over_roi_only_visual_reference",
        }

    def close(self):
        self.renderer.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def head_frame(sim, target, config=None):
    """Head RGB and the target's segmentation mask from the actual observation camera."""
    preparation_guard(sim)
    config = config or ViewConfig()
    with mujoco.Renderer(sim.model, height=config.height, width=config.width) as renderer:
        renderer.update_scene(sim.data, camera=sim.robot.camera_names[sim.robot.observation_camera])
        rgb = renderer.render().copy()
        mask = geom_mask(renderer, body_geoms(sim.model, target))
    ys, xs = np.nonzero(mask)
    return (
        rgb,
        mask,
        {
            "target": target,
            "visible_pixels": int(mask.sum()),
            "image_fraction": float(mask.mean()),
            "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None,
            "method": "actual_rendered_geom_segmentation_walls_not_hidden",
            "task_feasibility": "unknown",
        },
    )
