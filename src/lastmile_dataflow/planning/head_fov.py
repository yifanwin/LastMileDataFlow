"""Head optical frustum costs and adaptive, scratch-data-only validation.

FOV means target body origin inside the geometric frustum, not unoccluded pixels.
MuJoCo is used only for calibration/hard validation, never inside the optimizer.
"""
import copy
import math
import numpy as np


class HeadFOVCost:
    def __init__(self, tan_half_x, tan_half_y, *, margin=.9, min_depth=.01, weight=10000.):
        if not (all(math.isfinite(v) for v in (tan_half_x, tan_half_y, margin, min_depth, weight))
                and tan_half_x > 0 and tan_half_y > 0 and 0 < margin < 1 and min_depth > 0 and weight > 0):
            raise ValueError('invalid head FOV cost settings')
        self.tan_half_x, self.tan_half_y = tan_half_x, tan_half_y
        self.margin, self.min_depth, self.weight = margin, min_depth, weight

    def __call__(self, target_camera):
        import torch
        x, y, z = target_camera.unbind(-1)
        depth = -z
        loss = (torch.relu(x.abs() - self.margin * depth * self.tan_half_x).square()
                + torch.relu(y.abs() - self.margin * depth * self.tan_half_y).square()
                + torch.relu(self.min_depth - depth).square())
        return (self.weight * loss).unsqueeze(-1)  # [batch, horizon, 1]


def camera_definition(sim, width, height):
    """Reuse the actual MuJoCo optical mount, not base heading."""
    import mujoco
    from .curobo import body_pose, pose7
    cid = sim.model.camera(sim.robot.camera_names['head_camera']).id
    if sim.model.cam_mode[cid] != int(mujoco.mjtCamLight.mjCAMLIGHT_FIXED):
        raise ValueError('head FOV requires a fixed mounted camera')
    if sim.model.cam_projection[cid] or np.any(sim.model.cam_sensorsize[cid]):
        raise ValueError('head FOV currently requires the existing perspective fovy camera')
    parent = sim.model.body(sim.robot.config.namespace + 'link_head_2').id
    body = int(sim.model.cam_bodyid[cid])
    while body != parent and body != 0:
        if sim.model.body_jntnum[body]:
            raise ValueError('camera mount has additional moving joints')
        body = int(sim.model.body_parentid[body])
    if body != parent:
        raise ValueError('head camera is not mounted on the head branch')
    optical = np.eye(4)
    optical[:3, 3] = sim.data.cam_xpos[cid]
    optical[:3, :3] = sim.data.cam_xmat[cid].reshape(3, 3)
    mount = np.linalg.solve(body_pose(sim, parent), optical)
    tan_y = math.tan(math.radians(float(sim.model.cam_fovy[cid])) / 2)
    return {'camera_id': cid, 'camera_name': sim.robot.camera_names['head_camera'],
            'parent_link': 'link_head_2', 'mount_pose': pose7(mount),
            'tan_half_y': tan_y, 'tan_half_x': tan_y * width / height,
            'width': width, 'height': height, 'fovy_deg': float(sim.model.cam_fovy[cid])}


def camera_robot_config(cfg, definition):
    """Separate FK model: camera is never a motion-planner pose goal."""
    result = copy.deepcopy(cfg)
    k = result['kinematics']
    k['extra_links']['head_fov_optical'] = {
        'parent_link_name': definition['parent_link'], 'link_name': 'head_fov_optical',
        'fixed_transform': definition['mount_pose'], 'joint_type': 'FIXED',
        'joint_name': 'head_fov_optical_joint'}
    k['tool_frames'] = list(k['tool_frames']) + ['head_fov_optical']
    # v0.8.0 quaternion-FK backward did not match finite differences for
    # base yaw in this model. Fixed optical/TCP basis probes use only cuRobo's
    # position-FK gradients; no upstream patch or handwritten robot FK.
    for frame in (k['tool_frames'][0], 'head_fov_optical'):
        for axis in range(3):
            name = f'{frame}_fov_axis_{axis}'
            offset = [0.,0.,0.]; offset[axis] = .1
            k['extra_links'][name] = {'parent_link_name':frame, 'link_name':name,
                'fixed_transform':offset + [1.,0.,0.,0.], 'joint_type':'FIXED', 'joint_name':name+'_joint'}
            k['tool_frames'].append(name)
    return result


def optical_pose_torch(poses, frame):
    """Recover an optical basis from differentiable cuRobo link positions."""
    import torch
    position = poses.position[..., poses.tool_frames.index(frame), :]
    columns = [poses.position[..., poses.tool_frames.index(f'{frame}_fov_axis_{axis}'), :] - position
               for axis in range(3)]
    return position, torch.stack(columns, dim=-1) / .1


def target_camera_torch(kinematics, joint_state, context):
    """cuRobo FK + torch only; all tensors stay on the optimizer device."""
    poses = kinematics.compute_kinematics(joint_state).tool_poses
    cam_position, cam_rotation = optical_pose_torch(poses, 'head_fov_optical')
    target = context['fov_target']
    if context.get('fov_attached_offset') is not None:
        position, rotation = optical_pose_torch(poses, context['fov_tcp_frame'])
        target = (rotation @ context['fov_attached_offset'][..., None]).squeeze(-1) + position
    return (cam_rotation.transpose(-1,-2) @ (target-cam_position)[...,None]).squeeze(-1)


def knot_sample_indices(solution, dense_dt, count):
    """B-spline controls are not physical poses: check their TIME boundaries."""
    if solution.knot is None or solution.knot_dt is None:
        # POSITION control-space: optimized rollout points are the knots.
        dt = float(solution.dt.reshape(-1)[0].item())
        total = solution.position.shape[-2]
    else:
        dt = float(solution.knot_dt.reshape(-1)[0].item())
        # Cubic B-splines include padded boundary knots beyond the active
        # control count. Cover knot times through the full returned duration.
        total = int(math.ceil((count-1)*dense_dt/dt)) + 1
    if not np.isfinite(dt) or dt <= 0 or dense_dt <= 0:
        raise ValueError('invalid FOV knot timing')
    return sorted({0, count - 1, *[min(count - 1, int(round(i * dt / dense_dt))) for i in range(total)]})


class SparseFOVValidator:
    """Adaptive key-state validation; never mutates sim.data or calls initialize."""
    def __init__(self, sim, definition, base_target_world, names, *, min_depth=.01):
        import mujoco
        self.sim, self.definition, self.base_target_world = sim, definition, base_target_world
        self.names, self.min_depth = names, min_depth
        self.scratch = mujoco.MjData(sim.model)
        self.target_world = sim.data.xpos[sim.target_id].copy()
        self.attached_offset = None
        self.side = 'left' if 'left_arm_0' in names else 'right'

    def project(self, point):
        import mujoco
        from ..robots.action import torso_joints
        data, sim = self.scratch, self.sim
        data.qpos[:] = sim.data.qpos
        if sim.model.nmocap:
            data.mocap_pos[:] = sim.data.mocap_pos
            data.mocap_quat[:] = sim.data.mocap_quat
        values = dict(zip(self.names, point))
        base = self.base_target_world(point)
        for n, q in zip(sim.robot.groups['base'], base):
            data.qpos[sim.robot.addresses[n]] = q
        for n, q in zip(sim.robot.groups['torso'], torso_joints(values['torso_1'])):
            data.qpos[sim.robot.addresses[n]] = q
        for n in sim.robot.groups[self.side + '_arm']:
            data.qpos[sim.robot.addresses[n]] = values[n]
        mujoco.mj_forward(sim.model, data)
        cid = self.definition['camera_id']
        position = data.cam_xpos[cid].copy()
        rotation = data.cam_xmat[cid].reshape(3, 3).copy()
        target = self.target_world
        if self.attached_offset is not None:
            site = sim.model.site(sim.robot.config.namespace + 'ee_site_' + self.side[0]).id
            target = data.site_xpos[site] + data.site_xmat[site].reshape(3, 3) @ self.attached_offset
        local = rotation.T @ (target - position)
        depth = -float(local[2])
        limits = depth * np.array([self.definition['tan_half_x'], self.definition['tan_half_y']])
        valid = bool(np.isfinite(local).all() and depth > self.min_depth and np.all(np.abs(local[:2]) < limits))
        edge = float(np.max(np.abs(local[:2]) / np.maximum(limits, 1e-12)))
        return {'valid': valid, 'edge_fraction': edge, 'depth_m': depth,
                'target_camera': local.tolist(), 'camera_position': position.tolist(),
                'camera_rotation': rotation.tolist()}

    def validate(self, points, key_indices, *, phase='pose'):
        points = np.asarray(points, dtype=float)
        if points.ndim != 2 or not len(points) or points.shape[1] != len(self.names) or not np.isfinite(points).all():
            raise ValueError('invalid FOV trajectory')
        keys = sorted({0, len(points)-1, *key_indices})
        if any(i < 0 or i >= len(points) for i in keys):
            raise ValueError('FOV key index outside trajectory')
        # Arm-only motion does not change camera pose. A carried target DOES move.
        relevant = [self.names.index(n) for n in ('base_x', 'base_y', 'base_theta', 'torso_1')]
        if self.attached_offset is not None:
            relevant += [self.names.index(n) for n in self.sim.robot.groups[self.side + '_arm']]
        cache, checked, reused = {}, {}, []
        def check(i):
            if i in checked:
                return checked[i]
            signature = tuple(points[i, relevant])
            if signature in cache:
                checked[i] = cache[signature]; reused.append(i)
            else:
                checked[i] = self.project(points[i]); cache[signature] = checked[i]
            return checked[i]
        for i in keys:
            check(i)
        thresholds = np.array([.05, .05, .08, .04] + ([.08] * 7 if self.attached_offset is not None else []))
        def refine(a, b):
            if b-a <= 1 or not check(a)['valid'] or not check(b)['valid']:
                return
            block = points[a:b+1, relevant]
            moving = np.any(np.ptp(block, axis=0) > 1e-8)
            if not moving:
                return
            ca, cb = check(a), check(b)
            rotation = np.asarray(ca['camera_rotation']).T @ np.asarray(cb['camera_rotation'])
            angle = math.acos(float(np.clip((np.trace(rotation)-1)/2, -1, 1)))
            large = (np.any(np.ptp(block, axis=0) > thresholds)
                     or np.linalg.norm(np.subtract(ca['camera_position'], cb['camera_position'])) > .05 or angle > .08)
            near_edge = max(ca['edge_fraction'], cb['edge_fraction']) > .9
            if large or near_edge:
                mid = (a+b)//2
                if check(mid)['valid']:
                    refine(a, mid); refine(mid, b)
        for a, b in zip(keys, keys[1:]):
            refine(a, b)
        failures = [i for i in sorted(checked) if not checked[i]['valid']]
        return {'valid': not failures, 'status': 'PASS' if not failures else 'FOV_CONSTRAINT_FAILED',
                'phase': phase, 'key_indices': keys, 'checked_indices': sorted(checked),
                'geometry_checks': len(cache), 'reused_camera_states': len(set(reused)),
                'trajectory_points': len(points), 'failed_indices': failures,
                'checks': {str(i): checked[i] for i in sorted(checked)},
                'scope': 'sparse geometric target-origin frustum; not occlusion or continuous-time proof'}
