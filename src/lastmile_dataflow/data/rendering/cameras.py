"""Render-only cameras: free-camera rigs, the robot/target observer and segmentation masks.

Nothing here changes the MJCF, physics state or policy observations.
"""
from dataclasses import asdict, dataclass
import itertools
import math

import mujoco
import numpy as np

from ...scenes.geometry import descendants


@dataclass(frozen=True)
class ViewConfig:
    width: int = 640
    height: int = 480
    fovy_rad: float = math.pi/4

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.width, self.height)):
            raise ValueError('positive image dimensions required')
        if not math.isfinite(self.fovy_rad) or not .1 < self.fovy_rad < 2.5:
            raise ValueError('invalid fovy_rad')


@dataclass(frozen=True)
class CameraRig:
    lookat: list
    distance_m: float
    fovy_rad: float
    width: int
    height: int
    # Radians internally; only the MuJoCo free-camera adapter converts to degrees.
    angles: tuple = ((0., -math.pi/2+.01), (math.pi/4, -.65), (-3*math.pi/4, -.65))

    @classmethod
    def fit(cls, points, config=None, *, expansion=1.):
        config = config or ViewConfig()
        points = np.asarray(points)
        if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
            raise ValueError('finite union framing points required')
        lo, hi = points.min(axis=0), points.max(axis=0)
        center = (lo+hi)/2
        radius = max(float(np.linalg.norm(hi-lo)/2), .15)
        half_angle = min(config.fovy_rad/2, math.atan(math.tan(config.fovy_rad/2)*config.width/config.height))
        distance = radius/math.sin(half_angle)*1.25*expansion
        return cls(center.tolist(), distance, config.fovy_rad, config.width, config.height)

    def camera(self, index):
        cam = mujoco.MjvCamera()
        cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        cam.lookat = self.lookat
        cam.distance = self.distance_m
        cam.azimuth, cam.elevation = [math.degrees(a) for a in self.angles[index]]
        return cam

    def to_dict(self):
        return asdict(self)


def body_geoms(model, body):
    """Geoms of a body and all its descendants."""
    return np.flatnonzero(np.isin(model.geom_bodyid, list(descendants(model, model.body(body).id))))


def dense_segids(scene):
    # A compact ROI scene needs dense segids for Renderer's scene.ngeom lookup;
    # objid continues to identify the original model geometry.
    for index, geom in enumerate(scene.geoms[:scene.ngeom]):
        if geom.segid != -1:
            geom.segid = index


def geom_mask(renderer, geoms):
    """Pixels showing any of `geoms` in the renderer's current scene."""
    dense_segids(renderer.scene)
    renderer.enable_segmentation_rendering()
    try:
        segmentation = renderer.render()
    finally:
        renderer.disable_segmentation_rendering()
    return ((segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM))
            & np.isin(segmentation[:, :, 0], geoms))


def rendered_camera(renderer, width, height):
    """Optical pose (x right, y down, z forward) and intrinsics of the actual GL camera."""
    camera = mujoco.mjv_averageCamera(renderer.scene.camera[0], renderer.scene.camera[1])
    forward = np.asarray(camera.forward, dtype=float)
    forward /= np.linalg.norm(forward)
    up = np.asarray(camera.up, dtype=float)
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    transform = np.eye(4)
    transform[:3, :3] = np.column_stack((right, -up, forward))
    transform[:3, 3] = camera.pos
    near = float(camera.frustum_near)
    bottom, top = float(camera.frustum_bottom), float(camera.frustum_top)
    center = float(camera.frustum_center)
    # MuJoCo determines the viewport width from vertical frustum and aspect.
    half_width = (top - bottom) * width / height / 2
    orthographic = bool(camera.orthographic)
    fy = height * (1 if orthographic else near) / (top - bottom)
    fx = width * (1 if orthographic else near) / (2 * half_width)
    # Pixel centres are integer coordinates; left edge is -0.5.
    cx = width * (half_width - center) / (2 * half_width) - .5
    cy = height * top / (top - bottom) - .5
    return {'projection_type': 'orthographic' if orthographic else 'pinhole',
            'K': [[fx, 0, cx], [0, fy, cy], [0, 0, 1]], 'near_m': near,
            'T_world_camera_optical': transform.tolist()}


@dataclass(frozen=True)
class ThirdPersonConfig:
    context_margin_m: float = .35
    elevations_deg: tuple = (-40., -70.)
    azimuths_deg: tuple = (45., 135., 225., 315., 0., 90., 180., 270.)

    def __post_init__(self):
        if not math.isfinite(self.context_margin_m) or self.context_margin_m < 0:
            raise ValueError('invalid context margin')
        if not self.elevations_deg or not self.azimuths_deg or not all(
                math.isfinite(v) for v in (*self.elevations_deg, *self.azimuths_deg)):
            raise ValueError('invalid observer angles')


class ThirdPersonCamera:
    """Fit robot+target with context; choose visible initial angle, then track union.

    Angles remain fixed per attempt, avoiding arbitrary camera jumps. Occlusion
    is real: never hide walls/objects to manufacture visibility.
    """
    name = 'third_person_camera'

    def __init__(self, config=None):
        self.config = config or ThirdPersonConfig()
        self.angle = None
        self.rig = None
        self.initial_visibility = None

    def framing(self, sim, width, height):
        m, d = sim.model, sim.data
        robot = {b for b in range(m.nbody) if m.body(b).name.startswith(sim.robot.config.namespace)}
        target = descendants(m, sim.target_id) if sim.target_id is not None else set()
        robot_geoms, target_geoms, points = [], [], []
        corners = np.array(list(itertools.product((-1., 1.), repeat=3)))
        for g in range(m.ngeom):
            b = int(m.geom_bodyid[g])
            if b not in robot and b not in target:
                continue
            if b in robot:
                robot_geoms.append(g)
            if b in target:
                target_geoms.append(g)
            radius = float(m.geom_rbound[g]) + self.config.context_margin_m
            points.extend(d.geom_xpos[g] + corners * radius)
        if not points:
            raise ValueError('no robot/target geometry to frame')
        config = ViewConfig(width=width, height=height, fovy_rad=math.radians(float(m.vis.global_.fovy)))
        return CameraRig.fit(np.asarray(points), config), robot_geoms, target_geoms

    def _choose_angle(self, sim, renderer, fit, robot_geoms, target_geoms):
        """Initial angle with the most robot and target pixels in segmentation."""
        best = None
        renderer.enable_segmentation_rendering()
        try:
            for elevation in self.config.elevations_deg:
                for azimuth in self.config.azimuths_deg:
                    angle = (math.radians(azimuth), math.radians(elevation))
                    rig = CameraRig(fit.lookat, fit.distance_m, fit.fovy_rad, fit.width, fit.height, (angle,))
                    renderer.update_scene(sim.data, camera=rig.camera(0))
                    seg = renderer.render()
                    geoms = seg[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)
                    rp = int(np.count_nonzero(geoms & np.isin(seg[:, :, 0], robot_geoms)))
                    tp = int(np.count_nonzero(geoms & np.isin(seg[:, :, 0], target_geoms)))
                    score = (min(rp/300., 1.)*min(tp/24., 1.), min(rp, tp), rp+tp)
                    if best is None or score > best[0]:
                        best = (score, angle, rp, tp)
        finally:
            renderer.disable_segmentation_rendering()
        self.angle = best[1]
        self.initial_visibility = {'robot_pixels': best[2], 'target_pixels': best[3],
                                   'both_visible': best[2] > 0 and best[3] > 0}

    def render(self, sim, renderer, width, height):
        fit, robot_geoms, target_geoms = self.framing(sim, width, height)
        if self.angle is None:
            self._choose_angle(sim, renderer, fit, robot_geoms, target_geoms)
        self.rig = CameraRig(fit.lookat, fit.distance_m, fit.fovy_rad, width, height, (self.angle,))
        renderer.update_scene(sim.data, camera=self.rig.camera(0))
        return renderer.render().copy()

    def metadata(self):
        return {'name': self.name, 'kind': 'render_only_free_camera', 'mode': 'follow_robot_target_union',
                'angle_policy': 'initial segmentation selection; fixed angles within attempt',
                'config': asdict(self.config), 'rig': self.rig.to_dict() if self.rig else None,
                'initial_visibility': self.initial_visibility, 'scene_geometry_hidden': False}
