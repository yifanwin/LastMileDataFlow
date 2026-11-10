"""Tensor facts, observations and manager terms for the fixed-base pick task."""
import numpy as np
import torch
from mjlab.utils.lab_api.math import compute_pose_error, quat_from_matrix

from ...scenes.rl_observation import GeometryObservation
from ...scenes.stations.rl import prepare_stations
from ..outcomes.pick_state import PICK_THRESHOLDS as P, update_hold_window


def contact_addresses(model, prefixes, field, device):
    # One native sensor per field with num_slots=1. Force offset zero is the
    # contact-frame normal force; never use the tangential components' norm.
    return torch.tensor([int(model.sensor_adr[i]) for i in range(model.nsensor)
                         if model.sensor(i).name.startswith(prefixes) and
                         model.sensor(i).name.endswith('_' + field)], dtype=torch.long, device=device)


class TaskState:
    def __init__(self, env):
        self.env = env
        self.device = env.device
        self.model = env.sim.mj_model
        self.robot = env.scene['robot']
        n = env.num_envs
        self.target = self.model.body(env.prepared.target_body).id
        self.finger_addresses = contact_addresses(self.model, ('finger_',), 'force', self.device)
        self.support_addresses = contact_addresses(self.model, ('support_',), 'force', self.device)
        self.collision_addresses = contact_addresses(self.model, ('environment_', 'nonfinger_', 'self_collision_'),
                                                      'found', self.device)
        self.collision_names = [self.model.sensor(i).name.removesuffix('_found') for i in range(self.model.nsensor)
                                if self.model.sensor(i).name.startswith(('environment_', 'nonfinger_', 'self_collision_'))
                                and self.model.sensor(i).name.endswith('_found')]
        self.tcp = self.model.site('robot/robot_0/gripper0_right_grip_site').id
        self.base = self.model.site('robot/robot_0/base_site').id
        self.arm, _ = self.robot.find_joints([f'robot0_joint{i}' for i in range(1, 8)], preserve_order=True)
        self.fingers, _ = self.robot.find_joints(['gripper0_right_finger_joint1', 'gripper0_right_finger_joint2'], preserve_order=True)
        self.fixed, _ = self.robot.find_joints(['mobilebase0_joint_mobile_forward', 'mobilebase0_joint_mobile_side',
                                               'mobilebase0_joint_mobile_yaw', 'mobilebase0_joint_torso_height'], preserve_order=True)
        self.graph = GeometryObservation(self.model, self.target, env.prepared.target_position,
                                         env.research_config['scene']['local_radius_m'], self.device)
        self.stations, initial = prepare_stations(self.model, env.prepared, env.research_config)
        self.initial_qpos = torch.tensor(initial, device=self.device, dtype=torch.float32)
        self.station_poses = torch.tensor([[s['x'], s['y'], s['yaw']] for s in self.stations], device=self.device)
        # A non-empty debug list narrows training for algorithm checks only; it is never an acceptance input.
        debug = set(env.research_config['training']['debug_station_ids'])
        self.pool = torch.tensor([i for i, s in enumerate(self.stations) if s['valid'] and s['split'] == 'train'
                                  and (not debug or s['station_id'] in debug)],
                                  device=self.device, dtype=torch.long)
        if not len(self.pool):
            raise ValueError('No physically valid training stations')
        self.station_ids = torch.zeros(n, dtype=torch.long, device=self.device)
        self.requested_station_ids = None
        self.fixed_reference = torch.zeros(n, 4, device=self.device)
        self.height_reference = torch.zeros(n, device=self.device)
        self.elapsed = torch.zeros(n, dtype=torch.float64, device=self.device)
        self.window_start = torch.full((n,), -1., dtype=torch.float64, device=self.device)
        self.reference_position = torch.zeros(n, 3, device=self.device)
        self.reference_rotation = torch.eye(3, device=self.device).repeat(n, 1, 1)
        self.hold = torch.zeros(n, dtype=torch.float64, device=self.device)
        self.forces = torch.zeros(n, 2, device=self.device)
        self.support = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.success = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.failed = torch.zeros_like(self.success)
        self.control_failed = torch.zeros_like(self.success)
        self.unhealthy = torch.zeros_like(self.success)
        self.step_collision = torch.zeros_like(self.success)
        self.collision_free = torch.ones_like(self.success)
        self.collision_time = torch.zeros(n, device=self.device)
        self.collision_hits = torch.zeros(n, len(self.collision_addresses), device=self.device)
        self.max_drift = torch.zeros(n, device=self.device)
        self.max_yaw_drift = torch.zeros(n, device=self.device)
        self.max_lift_drift = torch.zeros(n, device=self.device)
        self.last_action = torch.zeros(n, 7, device=self.device)
        self.previous_distance = torch.zeros(n, device=self.device)
        self.previous_angle = torch.zeros(n, device=self.device)
        self.previous_grasp = torch.zeros(n, device=self.device)
        self.needs_baseline = torch.ones(n, dtype=torch.bool, device=self.device)
        self.reward_components = torch.zeros(n, 7, device=self.device)
        # The fixed object-local TCP is an explicit input, shared with scoring.
        from ...scenes.loading.rl_scene import RLSceneConfig
        scene_config = RLSceneConfig(**env.research_config['scene'])
        grasp = env.research_config['grasp']
        with np.load(scene_config.asset(grasp['path']), allow_pickle=False) as source:
            pose = source['transforms'][grasp['row']].astype(float)
        # The corpus stores quantized rotations, as in the existing grasp loader.
        u, _, v = np.linalg.svd(pose[:3, :3])
        pose[:3, :3] = u @ v
        pose[:3, 3] += pose[:3, 2] * grasp['approach_offset_m']
        angle = grasp['roll_rad']
        pose[:3, :3] = pose[:3, :3] @ np.array([[np.cos(angle), -np.sin(angle), 0.],
                                                [np.sin(angle), np.cos(angle), 0.], [0., 0., 1.]])
        self.grasp_position = torch.tensor(pose[:3, 3], dtype=torch.float32, device=self.device)
        self.grasp_rotation = torch.tensor(pose[:3, :3], dtype=torch.float32, device=self.device)

    def poses(self):
        data = self.env.sim.data
        tcp_p = data.site_xpos[:, self.tcp]
        tcp_r = data.site_xmat[:, self.tcp].reshape(-1, 3, 3)
        base_p = data.site_xpos[:, self.base]
        base_r = data.site_xmat[:, self.base].reshape(-1, 3, 3)
        target_p = data.xpos[:, self.target]
        target_r = data.xmat[:, self.target].reshape(-1, 3, 3)
        return tcp_p, tcp_r, base_p, base_r, target_p, target_r

    def goal_error(self, approach_offset_m=0.):
        tcp_p, tcp_r, _, base_r, target_p, target_r = self.poses()
        goal_p = target_p + torch.einsum('bij,j->bi', target_r, self.grasp_position)
        goal_r = target_r @ self.grasp_rotation
        goal_p = goal_p + approach_offset_m * goal_r[:, :, 2]
        dp, dr = compute_pose_error(tcp_p, quat_from_matrix(tcp_r), goal_p, quat_from_matrix(goal_r))
        return torch.einsum('bji,bj->bi', base_r, dp), torch.einsum('bji,bj->bi', base_r, dr)

    def grasp_potential(self):
        '''Staged grasp potential: close near the pose, bear on both fingers, lift while held.

        Rewarded only as a difference between policy steps (potential-based shaping),
        so it adds no return for loitering in any state.
        '''
        dp, _ = self.goal_error()
        q = self.robot.data.joint_pos
        width = q[:, self.fingers[0]] - q[:, self.fingers[1]]
        closed_near = (dp.norm(dim=-1) < .03).float() * (1 - width / .08).clamp(0, 1)
        both = (self.forces > P['finger_force_min_n']).all(-1).float()
        lift = ((self.env.sim.data.xpos[:, self.target, 2] - self.height_reference) / P['lift_m']).clamp(0, 1)
        return .5 * closed_near + both + 2 * both * lift

    def begin_step(self, action):
        self.step_collision.zero_()
        self.last_action[:] = action

    def contact_facts(self):
        values = self.env.sim.data.sensordata
        forces = values[:, self.finger_addresses].clamp(min=0)
        support = (values[:, self.support_addresses] > P['finger_force_min_n']).any(-1)
        return forces, support, values[:, self.collision_addresses] > 0

    def sample_physics(self):
        # All xpos/xmat/sensor forces are pre-integration data from the same
        # mj_step. Do not call forward() here and mix their timestamps.
        tcp_p, tcp_r, _, _, target_p, target_r = self.poses()
        forces, support, hits = self.contact_facts()
        collision = hits.any(-1)
        self.collision_hits += hits.float() * self.env.physics_dt
        self.forces[:] = forces
        self.support[:] = support
        self.step_collision |= collision
        self.collision_free &= ~collision
        self.collision_time += collision.float() * self.env.physics_dt
        relative_p = torch.einsum('bji,bj->bi', tcp_r, target_p - tcp_p)
        relative_r = tcp_r.transpose(-1, -2) @ target_r
        lift = target_p[:, 2] - self.height_reference
        held = (self.forces > P['finger_force_min_n']).all(-1) & ~support & (lift >= P['lift_m'])
        self.window_start, self.reference_position, self.reference_rotation, self.hold = update_hold_window(
            self.elapsed, held, relative_p, relative_r, self.window_start,
            self.reference_position, self.reference_rotation, xp=torch)
        fixed = self.robot.data.joint_pos[:, self.fixed]
        delta = fixed - self.fixed_reference
        drift = delta[:, :2].norm(dim=-1)
        self.max_drift = torch.maximum(self.max_drift, drift)
        yaw = torch.atan2(delta[:, 2].sin(), delta[:, 2].cos()).abs()
        self.max_yaw_drift = torch.maximum(self.max_yaw_drift, yaw)
        self.max_lift_drift = torch.maximum(self.max_lift_drift, delta[:, 3].abs())
        active = ~(self.success | self.failed | self.unhealthy)
        fixed_failure = ((drift > P['base_translation_m']) | (yaw > P['base_yaw_rad']) |
                         (delta[:, 3].abs() > P['lift_hold_error_m']))
        self.control_failed |= active & fixed_failure
        self.failed |= active & (fixed_failure | (lift < -.3))
        data = self.env.sim.data
        self.unhealthy |= ((data.overflow != 0) | ~(torch.isfinite(data.qpos).all(-1) &
                             torch.isfinite(data.qvel).all(-1) & torch.isfinite(data.qacc).all(-1)))
        self.success |= active & (self.hold >= P['hold_s'] - 1e-9) & ~self.failed & ~self.unhealthy
        self.elapsed += self.env.physics_dt

    def observation_state(self):
        tcp_p, tcp_r, base_p, base_r, target_p, target_r = self.poses()
        dp, dr = self.goal_error()
        distance, angle = dp.norm(dim=-1), dr.norm(dim=-1)
        forces, support, _ = self.contact_facts()
        self.forces[:] = torch.where(self.needs_baseline[:, None], forces, self.forces)
        self.support[:] = torch.where(self.needs_baseline, support, self.support)
        self.previous_distance[self.needs_baseline] = distance[self.needs_baseline]
        self.previous_angle[self.needs_baseline] = angle[self.needs_baseline]
        self.height_reference[self.needs_baseline] = target_p[self.needs_baseline, 2]
        self.previous_grasp[self.needs_baseline] = self.grasp_potential()[self.needs_baseline]
        self.needs_baseline.zero_()
        q, v = self.robot.data.joint_pos, self.robot.data.joint_vel
        width = (q[:, self.fingers[0]] - q[:, self.fingers[1]])[:, None]
        width_velocity = (v[:, self.fingers[0]] - v[:, self.fingers[1]])[:, None]
        tcp_local_p = torch.einsum('bji,bj->bi', base_r, tcp_p - base_p)
        tcp_local_r = base_r.transpose(-1, -2) @ tcp_r
        target_velocity = self.env.sim.data.cvel[:, self.target]
        target_angular = torch.einsum('bji,bj->bi', base_r, target_velocity[:, :3])
        root = int(self.model.body_rootid[self.target])
        target_linear_world = target_velocity[:, 3:] + torch.cross(
            target_velocity[:, :3], target_p - self.env.sim.data.subtree_com[:, root], dim=-1)
        target_linear = torch.einsum('bji,bj->bi', base_r, target_linear_world)
        relative_p = torch.einsum('bji,bj->bi', tcp_r, target_p - tcp_p)
        relative_r = tcp_r.transpose(-1, -2) @ target_r
        slip_p = relative_p - self.reference_position
        slip_r = self.reference_rotation.transpose(-1, -2) @ relative_r
        remaining = (self.env.cfg.episode_length_s - self.elapsed) / self.env.cfg.episode_length_s
        return torch.cat((q[:, self.arm] / 3.14, v[:, self.arm] / 3., tcp_local_p,
                          tcp_local_r[..., :2].transpose(-1, -2).flatten(-2), width / .08,
                          width_velocity / .2, dp, dr / 3.14, target_linear, target_angular / 5.,
                          (target_p[:, 2] - self.height_reference)[:, None],
                          torch.log1p(self.forces) / 5., self.support.float()[:, None],
                          (self.hold / P['hold_s'])[:, None], slip_p,
                          slip_r[..., :2].transpose(-1, -2).flatten(-2), remaining[:, None],
                          q[:, self.fixed] - self.fixed_reference,
                          torch.stack((self.previous_distance, self.previous_angle), -1),
                          self.last_action), -1).float()


def nodes(env):
    _, _, position, rotation, _, _ = env.task.poses()
    return env.task.graph.compute(env.sim.data, position, rotation)


def node_mask(env):
    return torch.ones(env.num_envs, env.task.graph.capacity, dtype=torch.bool, device=env.device)


def arm_state(env):
    return env.task.observation_state()


def reset(env, env_ids):
    task = env.task
    ids = torch.arange(env.num_envs, device=env.device) if env_ids is None else env_ids.long()
    selected = (task.pool[torch.randint(len(task.pool), (len(ids),), device=env.device)]
                if task.requested_station_ids is None else task.requested_station_ids)
    task.requested_station_ids = None
    task.station_ids[ids] = selected
    env.sim.data.qpos[ids] = task.initial_qpos
    env.sim.data.qvel[ids] = 0
    # Named robot indices select the four fixed-base/lift coordinates.
    q = task.robot.data.default_joint_pos[ids].clone()
    q[:, task.fixed[:3]] = task.station_poses[selected]
    task.robot.write_joint_state_to_sim(q, torch.zeros_like(q), env_ids=ids)
    task.robot.set_joint_position_target(q, env_ids=ids)
    task.fixed_reference[ids] = q[:, task.fixed]
    task.window_start[ids] = -1
    task.reference_position[ids] = 0
    task.reference_rotation[ids] = torch.eye(3, device=env.device)
    for name in ('elapsed', 'hold', 'forces', 'support', 'success', 'failed', 'unhealthy',
                 'control_failed', 'step_collision', 'collision_time', 'collision_hits', 'max_drift', 'max_yaw_drift',
                 'max_lift_drift', 'last_action', 'reward_components'):
        getattr(task, name)[ids] = 0
    task.collision_free[ids] = True
    task.needs_baseline[ids] = True


def substep(env):
    env.task.sample_physics()
    return env.task.hold


def reward(env):
    task = env.task
    dp, dr = task.goal_error()
    distance, angle = dp.norm(dim=-1), dr.norm(dim=-1)
    grasp = task.grasp_potential()
    cfg = env.research_config['reward']
    task.reward_components[:] = torch.stack((
        cfg['position'] * (task.previous_distance - distance),
        cfg['rotation'] * (task.previous_angle - angle),
        -cfg['collision'] * task.step_collision.float(),
        -cfg['limit'] * env.action_manager.get_term('tcp').limit_fraction,
        -cfg['control_failure'] * task.control_failed.float(),
        cfg['success'] * task.success.float(),
        cfg['grasp'] * (grasp - task.previous_grasp)), -1)
    task.previous_distance[:] = distance
    task.previous_angle[:] = angle
    task.previous_grasp[:] = grasp
    return task.reward_components.sum(-1)


def terminated(env):
    return env.task.success | env.task.failed | env.task.unhealthy


def timeout(env):
    return env.episode_length_buf >= env.max_episode_length
