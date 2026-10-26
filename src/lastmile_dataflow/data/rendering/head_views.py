"""Actual head RGB/segmentation; observations are reused, never recentered after edits."""
import copy
import mujoco
import numpy as np

from ...runtime.construction.preparation import preparation_guard
from ...scenes.geometry import descendants, geom_points
from .edit_views import RenderError, ViewConfig


def camera_state(sim):
    name = sim.robot.camera_names[sim.robot.observation_camera]
    camera = sim.model.camera(name).id
    return {'name': name, 'position': sim.data.cam_xpos[camera].tolist(),
            'rotation_matrix': sim.data.cam_xmat[camera].reshape(3, 3).tolist(),
            'fovy_deg': float(sim.model.cam_fovy[camera])}


def robocasa_camera_calibration(sim):
    """Compare compiled bindings/local optics; record actual world extrinsics."""
    from ...runtime.control.panda_robocasa import ROBOCASA_CAMERAS
    records = {}
    for name, (parent, position, quaternion, fovy) in ROBOCASA_CAMERAS.items():
        camera = sim.model.camera(sim.robot.camera_names[name]).id
        actual_parent = sim.model.body(int(sim.model.cam_bodyid[camera])).name
        expected_quat = np.asarray(quaternion) / np.linalg.norm(quaternion)
        valid = (actual_parent == sim.robot.config.namespace + parent
                 and np.allclose(sim.model.cam_pos[camera], position, atol=1e-6, rtol=0)
                 and abs(np.dot(sim.model.cam_quat[camera], expected_quat)) > 1 - 1e-6
                 and abs(sim.model.cam_fovy[camera] - fovy) < 1e-6)
        records[name] = {'valid': bool(valid), 'parent': actual_parent,
                         'position_local': sim.model.cam_pos[camera].tolist(),
                         'quaternion_local_wxyz': sim.model.cam_quat[camera].tolist(),
                         'fovy_deg': float(sim.model.cam_fovy[camera]),
                         'position_world': sim.data.cam_xpos[camera].tolist(),
                         'rotation_world': sim.data.cam_xmat[camera].reshape(3, 3).tolist()}
    return {'valid': all(c['valid'] for c in records.values()), 'cameras': records,
            'reference': 'RoboCasa CAM_CONFIGS DEFAULT; Panda factory wrist fovy=75; no randomization'}


def roi_geoms(sim, task):
    """Open visibility refers to a handle, never the whole articulated cabinet."""
    body = task['target_body']
    if task['operation'] == 'open':
        body = task.get('handle_body')
        if not body or body == task['target_body'] and not task.get('explicit_handle'):
            return []
    bodies = descendants(sim.model, sim.model.body(body).id)
    return np.flatnonzero(np.isin(sim.model.geom_bodyid, list(bodies))).tolist()


def aggregate_visibility(cameras):
    known = [row['fraction'] for row in cameras.values() if row.get('fraction') is not None]
    complete = bool(cameras) and len(known) == len(cameras)
    return {'cameras': cameras, 'complete': complete,
            'value': max(known) if complete else None,
            'known_max': max(known) if known else None,
            'status': 'measured' if complete else 'partial' if known else 'unknown',
            'aggregation': 'max_over_cameras; low requires every camera known'}


def _dense_segids(scene):
    # A compact ROI scene needs dense segids for Renderer's scene.ngeom lookup;
    # objid continues to identify the original model geometry.
    for index, geom in enumerate(scene.geoms[:scene.ngeom]):
        if geom.segid != -1:
            geom.segid = index


def _outside_frustum(sim, camera, points, width, height):
    local = (points - sim.data.cam_xpos[camera]) @ sim.data.cam_xmat[camera].reshape(3, 3)
    depth = -local[:, 2]
    tangent = np.tan(np.deg2rad(sim.model.cam_fovy[camera]) / 2)
    near = sim.model.vis.map.znear * sim.model.stat.extent
    return bool(np.all(depth <= near) or np.all(local[:, 0] > depth * tangent * width / height)
                or np.all(local[:, 0] < -depth * tangent * width / height)
                or np.all(local[:, 1] > depth * tangent) or np.all(local[:, 1] < -depth * tangent))


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

    def _mask(self):
        _dense_segids(self.renderer.scene)
        self.renderer.enable_segmentation_rendering()
        flags = self.renderer.scene.flags.copy()
        try:
            segmentation = self.renderer.render()
        finally:
            np.copyto(self.renderer.scene.flags, flags)
        return ((segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM))
                & np.isin(segmentation[:, :, 0], self.roi))

    def _camera(self, name):
        sim, cfg, renderer = self.sim, self.config, self.renderer
        camera = sim.model.camera(sim.robot.camera_names[name]).id
        renderer.update_scene(sim.data, camera=camera)
        observed = int(self._mask().sum())
        roi = [copy.copy(g) for g in renderer.scene.geoms[:renderer.scene.ngeom]
               if g.objtype == int(mujoco.mjtObj.mjOBJ_GEOM) and g.objid in self.roi]
        # Compact only the temporary MjvScene. Copy all visual fields, including
        # mesh/material bindings, so the reference uses the exact same optics.
        fields = ('type', 'dataid', 'objtype', 'objid', 'category', 'matid', 'texcoord',
                  'size', 'pos', 'mat', 'rgba', 'emission', 'specular', 'shininess',
                  'reflectance', 'modelrbound', 'transparent')
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
        reference = int(self._mask().sum())
        points = np.concatenate([geom_points(sim, g) for g in self.roi])
        outside = _outside_frustum(sim, camera, points, cfg.width, cfg.height)
        fraction = None
        if reference >= cfg.min_reference_pixels:
            fraction = min(1., observed / reference)
            status = 'measured'
        elif outside:
            fraction, status = 0., 'out_of_view'
        else:
            status = 'insufficient_projection'
        return {'status': status, 'fraction': fraction, 'visible_pixels': observed,
                'reference_pixels': reference, 'min_reference_pixels': cfg.min_reference_pixels,
                'camera_position_world': sim.data.cam_xpos[camera].tolist(),
                'camera_rotation_world': sim.data.cam_xmat[camera].reshape(3, 3).tolist()}

    def measure(self, sim=None):
        original = self.sim
        if sim is not None:
            if sim.model is not original.model:
                raise ValueError('visibility renderer belongs to a different model')
            self.sim = sim
        preparation_guard(self.sim)
        cameras = {}
        try:
            for name in self.sim.robot.config.cameras:
                if not self.roi:
                    cameras[name] = {'status': 'interaction_roi_not_defined', 'fraction': None}
                    continue
                try:
                    cameras[name] = self._camera(name)
                except (mujoco.FatalError, RuntimeError, IndexError) as exc:
                    cameras[name] = {'status': 'unknown', 'fraction': None, 'reason': type(exc).__name__}
        finally:
            self.renderer.disable_segmentation_rendering()
            self.sim = original
        return {**aggregate_visibility(cameras), 'roi_geoms': self.roi,
                'segmentation_msaa_samples': 0,
                'method': 'same_pose_roi_segmentation_over_roi_only_visual_reference'}

    def close(self):
        self.renderer.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def ray_visibility(sim, task, config, *, max_points=32, trace=False):
    """Cheap geometric proxy from all cameras; never a rollout success claim."""
    geoms = roi_geoms(sim, task)
    if not geoms:
        return aggregate_visibility({name: {'fraction': None, 'status': 'interaction_roi_not_defined'}
                                     for name in sim.robot.config.cameras})
    points = np.concatenate([geom_points(sim, g) for g in geoms])
    points = points[np.linspace(0, len(points) - 1, min(max_points, len(points)), dtype=int)]
    cameras = {}
    for name in sim.robot.config.cameras:
        camera = sim.model.camera(sim.robot.camera_names[name]).id
        origin = sim.data.cam_xpos[camera]
        visible = 0
        rays = [{'end': point.tolist(), 'visible': False} for point in points] if trace else None
        for index, point in enumerate(points):
            if _outside_frustum(sim, camera, point[None, :], config.width, config.height):
                continue
            delta = point - origin
            distance = np.linalg.norm(delta)
            if distance == 0:
                continue
            hit = np.array([-1], dtype=np.int32)
            mujoco.mj_ray(sim.model, sim.data, origin, delta / distance, None, True, -1, hit)
            detected = bool(hit[0] in geoms)
            visible += int(detected)
            if trace:
                rays[index]['visible'] = detected
        cameras[name] = {'fraction': visible / len(points), 'status': 'proxy',
                         'sample_count': len(points), 'visible_rays': visible}
        if trace:
            cameras[name].update(origin=origin.tolist(), rays=rays)
    return {**aggregate_visibility(cameras), 'method': 'surface_points_frustum_and_first_hit_rays'}


def head_frame(sim, target, config=None, *, renderer=None):
    preparation_guard(sim)
    config = config or ViewConfig()
    own = renderer is None
    renderer = renderer or mujoco.Renderer(sim.model, height=config.height, width=config.width)
    try:
        renderer.disable_segmentation_rendering()
        renderer.update_scene(sim.data, camera=sim.robot.camera_names[sim.robot.observation_camera])
        rgb = renderer.render().copy()
        try:
            _dense_segids(renderer.scene)
            renderer.enable_segmentation_rendering()
            segmentation = renderer.render().copy()
        except IndexError:
            # 旧单相机观测保留分割失败的 unknown，不冒充目标不可见。
            renderer.disable_segmentation_rendering()
            return rgb, np.zeros(rgb.shape[:2], bool), {
                'target': target, 'visible_pixels': 0, 'image_fraction': 0.0,
                'bbox_xyxy': None, 'method': 'segmentation_unavailable',
                'task_feasibility': 'unknown'}
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
