"""Calibration from the actual rendered monoscopic GL camera, including observers."""
import math

import mujoco
import numpy as np


def rendered_camera(renderer, width, height):
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
    near, far = float(camera.frustum_near), float(camera.frustum_far)
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
    return {
        'resolution': [width, height], 'projection_type': 'orthographic' if orthographic else 'pinhole',
        'intrinsics': {'K': [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
                       'units': 'pixels' if not orthographic else 'pixels_per_metre',
                       'source': 'actual_renderer_frustum'},
        'fovy': None if orthographic else {'value': math.atan2(top, near) - math.atan2(bottom, near), 'unit': 'rad'},
        'distortion': {'model': 'none', 'coefficients': []}, 'near_m': near, 'far_m': far,
        'T_world_camera_optical': transform.tolist(),
        'optical_frame': 'x_right_y_down_z_forward', 'pixel_convention': 'top_left_integer_pixel_centres',
        'source': 'MuJoCo_renderer_average_GL_camera', 'uncertainty': {'status': 'simulator_exact'},
        'source_camera': {'position': np.asarray(camera.pos).tolist(), 'forward': forward.tolist(),
                          'up': up.tolist(), 'frustum_bottom': bottom, 'frustum_top': top,
                          'frustum_center': center, 'frustum_width': float(camera.frustum_width),
                          'orthographic': orthographic},
    }


def capture_sensors(sim, width, height, *, depth):
    """No stepping: RGB, depth and calibration share one actual scene boundary."""
    if sim.renderer is not None and (sim.renderer.width, sim.renderer.height) != (width, height):
        sim.renderer.close()
        sim.renderer = None
    if sim.renderer is None:
        sim.renderer = mujoco.Renderer(sim.model, height=height, width=width)
    renderer = sim.renderer
    result = {}
    cameras = list(sim.robot.camera_names.items())
    if sim.third_person_camera is not None:
        cameras.append((sim.third_person_camera.name, None))
    for alias, name in cameras:
        if name is None:
            rgb = sim.third_person_camera.render(sim, renderer, width, height)
        else:
            renderer.update_scene(sim.data, camera=name)
            rgb = renderer.render().copy()
        calibration = rendered_camera(renderer, width, height)
        if name is not None:
            camera_id = sim.model.camera(name).id
            calibration['simulator_camera_state'] = {
                'name': name, 'position_world_m': sim.data.cam_xpos[camera_id].copy(),
                'rotation_world_source': sim.data.cam_xmat[camera_id].reshape(3, 3).copy(),
                'source_axes': 'x_right_y_up_minus_z_forward',
                'source_to_optical_rotation': np.diag([1., -1., -1.]).tolist()}
        sample = {'rgb': rgb, 'calibration': calibration, 'depth': None, 'depth_error': None}
        if depth:
            try:
                if calibration['projection_type'] != 'pinhole':
                    raise ValueError('depth linearization for non-pinhole projection is not validated')
                renderer.enable_depth_rendering()
                sample['depth'] = renderer.render().copy()
            except Exception as exc:
                sample['depth_error'] = type(exc).__name__ + ':' + str(exc)
            finally:
                renderer.disable_depth_rendering()
        result[alias] = sample
    return result
