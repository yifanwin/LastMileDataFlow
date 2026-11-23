"""Measure frozen-cup visibility at the unchanged native observation cameras."""

from copy import copy

import mujoco
import numpy as np

from lastmile_dataflow.sim.cameras import body_geoms, geom_mask, rendered_camera
from lastmile_dataflow.sim.model.geometry import geom_points


def _bbox(mask):
    y, x = np.nonzero(mask)
    return [int(x.min()), int(y.min()), int(x.max()), int(y.max())] if len(x) else None


class RoboCasaPickViews:
    def __init__(self, runtime):
        sim = runtime.env.sim
        self.data = sim.data._data
        model = sim.model._model
        self.width = runtime.kwargs["camera_widths"]
        self.height = runtime.kwargs["camera_heights"]
        self.options = copy(sim._render_context_offscreen.vopt)
        self.reference = copy(self.options)
        self.reference.geomgroup[:] = 0
        self.reference.geomgroup[5] = 1
        target = body_geoms(model, runtime.env.objects[runtime.target].root_body)
        enabled = self.options.geomgroup[model.geom_group[target]].astype(bool)
        self.target = target[enabled & (model.geom_rgba[target, 3] > 0)]
        self.model = copy(model)
        self.model.vis.quality.offsamples = 0
        # Group 5 is reserved for the target on this temporary model only. Clip
        # the reference with the exact same camera before measuring occlusion.
        self.model.geom_group[self.model.geom_group == 5] = 4
        self.model.geom_group[self.target] = 5
        self.options.geomgroup[5] = 1
        self.framing_points = np.concatenate([geom_points(sim, g) for g in self.target])
        self.renderer = mujoco.Renderer(self.model, width=self.width, height=self.height)

    def measure(self, camera, *, min_pixels, min_fraction):
        renderer = self.renderer
        renderer.update_scene(self.data, camera=camera, scene_option=self.options)
        observed = geom_mask(renderer, self.target)
        optics = rendered_camera(renderer, self.width, self.height)
        renderer.update_scene(self.data, camera=camera, scene_option=self.reference)
        reference = geom_mask(renderer, self.target)
        n_ref, n_seen = int(reference.sum()), int(observed.sum())
        transform, intrinsic = np.asarray(optics["T_world_camera_optical"]), np.asarray(optics["K"])
        optical = (self.framing_points - transform[:3, 3]) @ transform[:3, :3]
        projected = None
        if np.all(optical[:, 2] > optics["near_m"]):
            pixels = optical @ intrinsic.T
            projected = pixels[:, :2] / pixels[:, 2, None]
        bbox = None if projected is None else np.r_[projected.min(axis=0), projected.max(axis=0)]
        in_frame = bbox is not None and np.all(bbox[:2] >= -0.5) and np.all(
            bbox[2:] <= [self.width - 0.5, self.height - 0.5]
        )
        seen_fraction = min(n_seen / n_ref, 1.0) if n_ref else None
        if not n_ref:
            status = "out_of_frame"
        elif not in_frame:
            status = "partly_out_of_frame"
        elif n_ref < min_pixels:
            status = "too_small"
        elif seen_fraction < min_fraction:
            status = "occluded"
        else:
            status = "visible"
        return {
            "status": status, "reference_pixels": n_ref, "visible_pixels": n_seen,
            "visible_fraction": seen_fraction, "occluded_fraction": None if seen_fraction is None else 1 - seen_fraction,
            "reference_bbox_px": _bbox(reference), "visible_bbox_px": _bbox(observed),
            "unclipped_bbox_px": bbox, "unclipped_center_px": None if bbox is None else (bbox[:2] + bbox[2:]) / 2,
        }

    def overhead(self, geometry, obstacle_body):
        cam = mujoco.MjvCamera()
        cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        origin, axes = np.asarray(geometry["origin_world"]), np.asarray(geometry["axes_world"])
        cam.lookat = origin + axes @ [-0.14, 0.03, 0.045]
        cam.distance = 0.94
        cam.azimuth = np.degrees(np.arctan2(axes[1, 1], axes[0, 1]))
        cam.elevation = -79
        self.renderer.update_scene(self.data, camera=cam, scene_option=self.options)
        rgb = self.renderer.render().copy()
        obstacle_pixels = 0
        if obstacle_body:
            obstacle_pixels = int(geom_mask(self.renderer, body_geoms(self.model, obstacle_body)).sum())
        cup_pixels = int(geom_mask(self.renderer, self.target).sum())
        return rgb, obstacle_pixels, cup_pixels

    def obstacle_pixels(self, camera, body):
        self.renderer.update_scene(self.data, camera=camera, scene_option=self.options)
        return int(geom_mask(self.renderer, body_geoms(self.model, body)).sum())

    def close(self):
        self.renderer.close()
