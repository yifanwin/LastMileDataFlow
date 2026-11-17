"""Tensor facts, observations and manager terms for the fixed-base pick task."""

import numpy as np
import torch
from mjlab.utils.lab_api.math import axis_angle_from_quat, compute_pose_error, quat_from_matrix

from .observation import (
    GeometryObservation, REWARD_COMPONENTS, CONTACT_REWARD_COMPONENTS,
    GRASP_POSITION_TOLERANCE_M, GRASP_ROTATION_TOLERANCE_RAD,
)
from .stations import prepare_stations, station_mocap_poses
from lastmile_dataflow.operation.grasps import load_grasps, select_grasps
from lastmile_dataflow.sim.simulation import Simulation
from lastmile_dataflow.sim.model.geometry import body_pose, tcp_pose
from copy import deepcopy
from lastmile_dataflow.operation.outcomes.pick_state import RL_PICK_THRESHOLDS as P, update_hold_window
from .scene import ROBOT_ENTITY, ROBOT_PREFIX, RLSceneConfig


def contact_addresses(model, prefixes, field, device):
    # One native sensor per field with num_slots=1. Force offset zero is the
    # contact-frame normal force; never use the tangential components' norm.
    return torch.tensor(
        [
            int(model.sensor_adr[i])
            for i in range(model.nsensor)
            if model.sensor(i).name.startswith(prefixes) and model.sensor(i).name.endswith("_" + field)
        ],
        dtype=torch.long,
        device=device,
    )


def finger_contact(env):
    """Use the task protocol's normal-force threshold for both fingers."""
    threshold = (
        env.research_config["success"]["finger_force_min_n"]
        if getattr(env.task, "contact_protocol", False) else P["finger_force_min_n"]
    )
    return (env.task.forces > threshold).all(-1)


def target_lift_height(env, target_position=None, target_rotation=None):
    """Cube clearance above its original tabletop; legacy tasks use center rise."""
    task = env.task
    if getattr(task, "contact_protocol", False):
        if target_position is None:
            *_, target_position, target_rotation = task.poses()
        half = env.research_config["scene"]["cube"]["size_m"] / 2
        extent_z = half * target_rotation[:, 2, :].abs().sum(-1)
        return target_position[:, 2] - extent_z - task.table_height_m
    return env.sim.data.xpos[:, task.target, 2] - task.height_reference


def grasp_events(both, previous_both, paid, progress, peak, near):
    event = ~previous_both & both & ~paid & near
    new_peak = torch.maximum(peak, progress)
    reward_progress = (both & previous_both).float() * (new_peak - peak)
    return event, paid | event, new_peak, reward_progress


def stage_peak_progress(progress, active, previous_active, peak, paid):
    """Track a stage-local peak and consume at most one episode of progress."""
    continuing = active & previous_active
    new_peak = torch.where(continuing, torch.maximum(peak, progress), progress)
    increment = torch.minimum(
        continuing.float() * (new_peak - peak).clamp(min=0), (1 - paid).clamp(min=0)
    )
    peak[:] = new_peak
    previous_active[:] = active
    paid += increment
    return increment


class TaskState:
    def __init__(self, env):
        self.env = env
        self.device = env.device
        self.model = env.sim.mj_model
        self.robot = env.scene[ROBOT_ENTITY]
        n = env.num_envs
        self.contact_protocol = env.research_config["success"].get("version") == "rl-pick-contact-v2"
        self.target = self.model.body(env.prepared.target_body).id
        self.finger_addresses = contact_addresses(self.model, ("finger_",), "force", self.device)
        self.support_addresses = contact_addresses(
            self.model, ("support_",), "found" if self.contact_protocol else "force", self.device
        )
        self.collision_addresses = contact_addresses(
            self.model, ("environment_", "nonfinger_", "self_collision_"), "found", self.device
        )
        self.collision_names = [
            self.model.sensor(i).name.removesuffix("_found")
            for i in range(self.model.nsensor)
            if self.model.sensor(i).name.startswith(("environment_", "nonfinger_", "self_collision_"))
            and self.model.sensor(i).name.endswith("_found")
        ]
        self.tcp = self.model.site(ROBOT_PREFIX + "gripper0_right_grip_site").id
        self.base = self.model.site(ROBOT_PREFIX + "base_site").id
        self.arm, _ = self.robot.find_joints([f"robot0_joint{i}" for i in range(1, 8)], preserve_order=True)
        self.fingers, _ = self.robot.find_joints(
            ["gripper0_right_finger_joint1", "gripper0_right_finger_joint2"], preserve_order=True
        )
        self.physical_fixed = env.research_config["scene"].get("fixed_base", False)
        self.base_joints = []
        if not self.physical_fixed:
            self.base_joints, _ = self.robot.find_joints(
                ["mobilebase0_joint_mobile_" + suffix for suffix in ("forward", "side", "yaw")],
                preserve_order=True,
            )
        self.lift, _ = self.robot.find_joints(["mobilebase0_joint_torso_height"])
        self.open_grip = float(
            self.model.joint(ROBOT_PREFIX + "gripper0_right_finger_joint1").range[1]
            - self.model.joint(ROBOT_PREFIX + "gripper0_right_finger_joint2").range[0]
        )
        self.lift_range = self.model.joint(ROBOT_PREFIX + "mobilebase0_joint_torso_height").range.copy()
        self.graph = None if self.contact_protocol else GeometryObservation(
            self.model,
            self.target,
            env.prepared.target_position,
            env.research_config["scene"]["local_radius_m"],
            self.device,
        )
        self.stations, initial = prepare_stations(self.model, env.prepared, env.research_config)
        self.initial_qpos = torch.tensor(initial, device=self.device, dtype=torch.float32)
        self.station_poses = torch.tensor([[s["x"], s["y"], s["yaw"]] for s in self.stations], device=self.device)
        if self.physical_fixed:
            self.mocap_poses = torch.tensor(
                station_mocap_poses(self.model, self.base, self.stations), device=self.device, dtype=torch.float32
            )
        # A non-empty debug list narrows training for algorithm checks only; it is never an acceptance input.
        debug = set(env.research_config["training"]["debug_station_ids"])
        self.pool = torch.tensor(
            [
                i
                for i, s in enumerate(self.stations)
                if s["valid"] and (not debug or s["station_id"] in debug)
            ],
            device=self.device,
            dtype=torch.long,
        )
        if not len(self.pool):
            raise ValueError("No physically valid training stations")
        self.station_ids = torch.zeros(n, dtype=torch.long, device=self.device)
        self.requested_station_ids = None
        self.fixed_reference = torch.zeros(n, 4, device=self.device)
        self.height_reference = torch.zeros(n, device=self.device)
        self.elapsed = torch.zeros(n, dtype=torch.float64, device=self.device)
        self.window_start = torch.full((n,), -1.0, dtype=torch.float64, device=self.device)
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
        self.contact_bonus_paid = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.previous_both = torch.zeros_like(self.contact_bonus_paid)
        self.lift_peak = torch.zeros(n, device=self.device)
        self.step_contact = torch.zeros(n, device=self.device)
        self.step_lift = torch.zeros(n, device=self.device)
        self.held_time = torch.zeros(n, device=self.device)
        self.needs_baseline = torch.ones(n, dtype=torch.bool, device=self.device)
        self.reward_components = torch.zeros(
            n, len(CONTACT_REWARD_COMPONENTS if self.contact_protocol else REWARD_COMPONENTS), device=self.device
        )
        self.contact_success_paid = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.first_contact_time_s = torch.full((n,), -1.0, device=self.device, dtype=torch.float64)
        if self.contact_protocol:
            # Cube creation places an upright box directly on the physical support.
            # Keep that tabletop fixed, independently of each episode's baseline.
            self.table_height_m = float(env.prepared.target_position[2]) - env.research_config["scene"]["cube"]["size_m"] / 2
            self.closing_active = torch.zeros(n, dtype=torch.bool, device=self.device)
            self.closing_peak = torch.zeros(n, device=self.device)
            self.closing_paid = torch.zeros(n, device=self.device)
            # The gripper's local Y axis closes across the cube's local X;
            # local Z points downward for the approach. Columns form a proper rotation.
            self.grasp_position = torch.zeros(n, 3, device=self.device)
            self.grasp_rotation = torch.tensor(
                [[0., 1., 0.], [1., 0., 0.], [0., 0., -1.]], device=self.device
            ).expand(n, -1, -1).clone()
            self.grasp_ids = torch.zeros(n, dtype=torch.long, device=self.device)
            return
        scene_config = RLSceneConfig(**env.research_config["scene"])
        grasp = env.research_config["grasp"]
        self.grasps = load_grasps(scene_config.asset(grasp["path"]), env.prepared.target_body)
        if not self.grasps:
            raise ValueError("No valid corpus grasps")
        angle = grasp["roll_rad"]
        roll = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        self.grasp_positions = torch.tensor(np.array([c["pose_local"][:3, 3] for c in self.grasps]), device=self.device, dtype=torch.float32)
        self.grasp_rotations = torch.tensor(np.array([c["pose_local"][:3, :3] @ roll for c in self.grasps]), device=self.device, dtype=torch.float32)
        self.grasp_ids = torch.zeros(n, device=self.device, dtype=torch.long)
        self.requested_grasp_ids = None
        self.grasp_position = torch.zeros(n, 3, device=self.device)
        self.grasp_rotation = torch.eye(3, device=self.device).repeat(n, 1, 1)
        sim = Simulation(self.model, scene_config.robot_config(entity=True), env.prepared.catalog, env.prepared.target_body)
        self.grasp_pools = []
        identities = {id(c): i for i, c in enumerate(self.grasps)}
        settled_qpos = self.initial_qpos.cpu().numpy()
        local_poses = np.array([c["pose_local"] for c in self.grasps])
        for station in self.stations:
            initial = deepcopy(scene_config.robot_config(entity=True).initial)
            initial["base_xyyaw"] = [station[k] for k in ("x", "y", "yaw")]
            sim.data.qpos[:] = settled_qpos
            sim.robot.initialize(initial)
            world = body_pose(sim, self.target) @ local_poses
            pool = select_grasps(self.grasps, world, tcp_pose(sim, sim.robot.arms[0])[:3, 3], 16, grasp.get("fps_position_scale_m", 0.1), grasp.get("fps_rotation_scale_rad", np.pi / 2))
            self.grasp_pools.append([identities[id(c)] for c in pool])
        sim.close()
        self.grasp_pool_counts = torch.tensor([len(p) for p in self.grasp_pools], device=self.device)
        self.grasp_pool_table = torch.tensor([p + [p[-1]] * (16 - len(p)) for p in self.grasp_pools], device=self.device)

    def poses(self):
        data = self.env.sim.data
        tcp_p = data.site_xpos[:, self.tcp]
        tcp_r = data.site_xmat[:, self.tcp].reshape(-1, 3, 3)
        base_p = data.site_xpos[:, self.base]
        base_r = data.site_xmat[:, self.base].reshape(-1, 3, 3)
        target_p = data.xpos[:, self.target]
        target_r = data.xmat[:, self.target].reshape(-1, 3, 3)
        return tcp_p, tcp_r, base_p, base_r, target_p, target_r

    def goal_error(self, approach_offset_m=0.0):
        tcp_p, tcp_r, _, base_r, target_p, target_r = self.poses()
        goal_p = target_p + torch.einsum("bij,bj->bi", target_r, self.grasp_position)
        goal_r = target_r @ self.grasp_rotation
        goal_p = goal_p + approach_offset_m * goal_r[:, :, 2]
        dp, dr = compute_pose_error(tcp_p, quat_from_matrix(tcp_r), goal_p, quat_from_matrix(goal_r))
        return torch.einsum("bji,bj->bi", base_r, dp), torch.einsum("bji,bj->bi", base_r, dr)

    def tcp_velocity(self):
        action = self.env.action_manager.get_term("tcp")
        action._point_torch[:] = self.env.sim.data.site_xpos[:, self.tcp]
        action._compute_jacobian()
        velocity = self.env.sim.data.qvel
        linear = torch.einsum("bij,bj->bi", action._jacp_torch, velocity)
        angular = torch.einsum("bij,bj->bi", action._jacr_torch, velocity)
        return self.base_frame_velocity(action._point_torch, linear, angular)

    def target_velocity(self):
        data = self.env.sim.data
        position = data.xpos[:, self.target]
        motion = data.cvel[:, self.target]
        root = int(self.model.body_rootid[self.target])
        angular = motion[:, :3]
        linear = motion[:, 3:] + torch.cross(angular, position - data.subtree_com[:, root], dim=-1)
        return self.base_frame_velocity(position, linear, angular)

    def base_frame_velocity(self, position, linear, angular):
        """Velocity of a point relative to the measured moving base frame."""
        _, _, base_p, base_r, _, _ = self.poses()
        data = self.env.sim.data
        body = int(self.model.site_bodyid[self.base])
        root = int(self.model.body_rootid[body])
        motion = data.cvel[:, body]
        omega = motion[:, :3]
        base_linear = motion[:, 3:] + torch.cross(omega, base_p - data.subtree_com[:, root], dim=-1)
        relative_linear = linear - base_linear - torch.cross(omega, position - base_p, dim=-1)
        return (
            torch.einsum("bji,bj->bi", base_r, relative_linear),
            torch.einsum("bji,bj->bi", base_r, angular - omega),
        )

    def begin_step(self, action):
        self.step_collision.zero_()
        self.step_contact.zero_()
        self.step_lift.zero_()
        self.held_time.zero_()
        self.last_action[:] = action

    def fixed_state(self):
        """Base XY/yaw and lift position, independent of the base's joint layout."""
        q = self.robot.data.joint_pos
        if self.physical_fixed:
            data = self.env.sim.data
            rotation = data.site_xmat[:, self.base].reshape(-1, 3, 3)
            yaw = torch.atan2(rotation[:, 1, 0], rotation[:, 0, 0])[:, None]
            base = torch.cat((data.site_xpos[:, self.base, :2], yaw), -1)
        else:
            base = q[:, self.base_joints]
        return torch.cat((base, q[:, self.lift]), -1)

    def record_base_drift(self):
        delta = self.fixed_state() - self.fixed_reference
        drift = delta[:, :2].norm(dim=-1)
        yaw = torch.atan2(delta[:, 2].sin(), delta[:, 2].cos()).abs()
        self.max_drift = torch.maximum(self.max_drift, drift)
        self.max_yaw_drift = torch.maximum(self.max_yaw_drift, yaw)
        self.max_lift_drift = torch.maximum(self.max_lift_drift, delta[:, 3].abs())
        translation_limit = 1e-6 if self.physical_fixed else P["base_translation_m"]
        yaw_limit = 1e-6 if self.physical_fixed else P["base_yaw_rad"]
        return (drift > translation_limit) | (yaw > yaw_limit)

    def contact_facts(self):
        values = self.env.sim.data.sensordata
        forces = values[:, self.finger_addresses].clamp(min=0)
        support_threshold = 0 if self.contact_protocol else P["finger_force_min_n"]
        support = (values[:, self.support_addresses] > support_threshold).any(-1)
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
        relative_p = torch.einsum("bji,bj->bi", tcp_r, target_p - tcp_p)
        relative_r = tcp_r.transpose(-1, -2) @ target_r
        lift = target_p[:, 2] - self.height_reference
        both = finger_contact(self.env)
        if self.contact_protocol:
            # The same native sensor sample must show force on both fingers.
            dp, dr = self.goal_error()
            cfg = self.env.research_config["reward"]
            near = (dp.norm(dim=-1) < cfg["grasp_distance_m"]) & (dr.norm(dim=-1) < cfg["grasp_angle_rad"])
            event = near & both & ~self.contact_bonus_paid
            self.step_contact += event.float()
            self.contact_bonus_paid |= event
            fixed_failure = self.record_base_drift()
            self.control_failed |= fixed_failure
            self.failed |= fixed_failure | (lift < -0.3)
            data = self.env.sim.data
            self.unhealthy |= (data.overflow != 0) | ~(
                torch.isfinite(data.qpos).all(-1) & torch.isfinite(data.qvel).all(-1)
                & torch.isfinite(data.qacc).all(-1)
            )
            first = both & (self.first_contact_time_s < 0)
            self.first_contact_time_s[first] = self.elapsed[first] + self.env.physics_dt
            clearance = target_lift_height(self.env, target_p, target_r)
            success_cfg = self.env.research_config["success"]
            lifted = clearance >= success_cfg["lift_m"]
            held = both & lifted & ~support & ~self.failed & ~self.unhealthy
            self.hold[:] = torch.where(held, self.hold + self.env.physics_dt, 0.0)
            self.success |= held & (self.hold >= success_cfg["hold_s"] - 1e-9)
            self.elapsed += self.env.physics_dt
            return
        dp, dr = self.goal_error()
        cfg = self.env.research_config["reward"]
        near = (dp.norm(dim=-1) < cfg["grasp_distance_m"]) & (dr.norm(dim=-1) < cfg["grasp_angle_rad"])
        progress = (lift / P["lift_m"]).clamp(0, 1)
        event, paid, peak, reward_progress = grasp_events(
            both, self.previous_both, self.contact_bonus_paid, progress, self.lift_peak, near
        )
        self.step_contact += event.float()
        self.contact_bonus_paid[:] = paid
        self.step_lift += reward_progress
        self.lift_peak[:] = peak
        self.previous_both[:] = both
        self.held_time += (both & ~support).float() * self.env.physics_dt
        held = both & ~support & (lift >= P["lift_m"])
        self.window_start, self.reference_position, self.reference_rotation, self.hold = update_hold_window(
            self.elapsed,
            held,
            relative_p,
            relative_r,
            self.window_start,
            self.reference_position,
            self.reference_rotation,
            xp=torch,
            thresholds=P,
        )
        active = ~(self.success | self.failed | self.unhealthy)
        fixed_failure = self.record_base_drift()
        self.control_failed |= active & fixed_failure
        self.failed |= active & (fixed_failure | (lift < -0.3))
        data = self.env.sim.data
        self.unhealthy |= (data.overflow != 0) | ~(
            torch.isfinite(data.qpos).all(-1) & torch.isfinite(data.qvel).all(-1) & torch.isfinite(data.qacc).all(-1)
        )
        self.success |= active & (self.hold >= P["hold_s"] - 1e-9) & ~self.failed & ~self.unhealthy
        self.elapsed += self.env.physics_dt

    def flat_state(self):
        """Return base-frame state, optionally followed by world XY/yaw and lift metres."""
        tcp_p, tcp_r, base_p, base_r, cube_p, cube_r = self.poses()
        dp, dr = self.goal_error()
        distance, angle = dp.norm(dim=-1), dr.norm(dim=-1)
        self.previous_distance[self.needs_baseline] = distance[self.needs_baseline]
        self.previous_angle[self.needs_baseline] = angle[self.needs_baseline]
        self.height_reference[self.needs_baseline] = cube_p[self.needs_baseline, 2]
        self.needs_baseline.zero_()
        q, v = self.robot.data.joint_pos, self.robot.data.joint_vel
        cube_local_r = base_r.transpose(-1, -2) @ cube_r
        tcp_local_r = base_r.transpose(-1, -2) @ tcp_r
        linear, angular = self.tcp_velocity()
        cube_linear, cube_angular = self.target_velocity()
        cube_position = torch.einsum("bji,bj->bi", base_r, cube_p - base_p)
        size = torch.full((self.env.num_envs, 3), self.env.research_config["scene"]["cube"]["size_m"], device=self.device)
        extent = torch.einsum("bij,bj->bi", cube_local_r.abs(), size / 2)
        state = torch.cat((
            cube_position,
            cube_local_r[:, :, :2].transpose(-1, -2).flatten(1),
            size,
            cube_position[:, 2:3] - extent[:, 2:3],
            dp / GRASP_POSITION_TOLERANCE_M,
            dr / GRASP_ROTATION_TOLERANCE_RAD,
            cube_linear,
            cube_angular,
            q[:, self.arm] / 3.14,
            (q[:, self.lift] - self.lift_range[0]) / (self.lift_range[1] - self.lift_range[0]),
            v[:, self.arm] / 3.0,
            v[:, self.lift] / 0.05,
            torch.einsum("bji,bj->bi", base_r, tcp_p - base_p),
            tcp_local_r[:, :, :2].transpose(-1, -2).flatten(1),
            linear,
            angular,
            ((q[:, self.fingers[0]] - q[:, self.fingers[1]]) / self.open_grip)[:, None],
            ((v[:, self.fingers[0]] - v[:, self.fingers[1]]) / 0.2)[:, None],
            self.last_action,
        ), -1).float()
        if self.env.research_config.get("observation", {}).get("world_state", False):
            yaw = torch.atan2(base_r[:, 1, 0], base_r[:, 0, 0])[:, None]
            state = torch.cat((state, base_p[:, :2], yaw, q[:, self.lift]), -1)
        return state

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
        self.needs_baseline.zero_()
        q, v = self.robot.data.joint_pos, self.robot.data.joint_vel
        width = (q[:, self.fingers[0]] - q[:, self.fingers[1]])[:, None]
        width_velocity = (v[:, self.fingers[0]] - v[:, self.fingers[1]])[:, None]
        tcp_local_p = torch.einsum("bji,bj->bi", base_r, tcp_p - base_p)
        tcp_local_r = base_r.transpose(-1, -2) @ tcp_r
        target_velocity = self.env.sim.data.cvel[:, self.target]
        target_angular = torch.einsum("bji,bj->bi", base_r, target_velocity[:, :3])
        root = int(self.model.body_rootid[self.target])
        target_linear_world = target_velocity[:, 3:] + torch.cross(
            target_velocity[:, :3], target_p - self.env.sim.data.subtree_com[:, root], dim=-1
        )
        target_linear = torch.einsum("bji,bj->bi", base_r, target_linear_world)
        relative_p = torch.einsum("bji,bj->bi", tcp_r, target_p - tcp_p)
        relative_r = tcp_r.transpose(-1, -2) @ target_r
        slip_p = relative_p - self.reference_position
        slip_r = self.reference_rotation.transpose(-1, -2) @ relative_r
        remaining = (self.env.cfg.episode_length_s - self.elapsed) / self.env.cfg.episode_length_s
        linear, angular = self.tcp_velocity()
        return torch.cat(
            (
                q[:, self.arm] / 3.14,
                v[:, self.arm] / 3.0,
                tcp_local_p,
                tcp_local_r[..., :2].transpose(-1, -2).flatten(-2),
                width / self.open_grip,
                width_velocity / 0.2,
                dp,
                dr / 3.14,
                target_linear,
                target_angular / 5.0,
                (target_p[:, 2] - self.height_reference)[:, None],
                torch.log1p(self.forces) / 5.0,
                self.support.float()[:, None],
                (self.hold / P["hold_s"])[:, None],
                slip_p,
                slip_r[..., :2].transpose(-1, -2).flatten(-2),
                remaining[:, None],
                self.fixed_state() - self.fixed_reference,
                torch.stack((self.previous_distance, self.previous_angle), -1),
                self.last_action,
                (q[:, self.lift] - self.lift_range[0]) / (self.lift_range[1] - self.lift_range[0]),
                v[:, self.lift] / 0.05,
                linear,
                angular,
                self.contact_bonus_paid.float()[:, None],
                self.lift_peak[:, None],
            ),
            -1,
        ).float()


def nodes(env):
    _, _, position, rotation, _, _ = env.task.poses()
    return env.task.graph.compute(env.sim.data, position, rotation)


def node_mask(env):
    return torch.ones(env.num_envs, env.task.graph.capacity, dtype=torch.bool, device=env.device)


def arm_state(env):
    return env.task.observation_state()


def flat(env):
    return env.task.flat_state()


def reset_grasp_targets(task, selected, ids):
    if task.requested_grasp_ids is None:
        offsets = (torch.rand(len(ids), device=task.device) * task.grasp_pool_counts[selected]).long()
        task.grasp_ids[ids] = task.grasp_pool_table[selected, offsets]
    else:
        task.grasp_ids[ids] = task.requested_grasp_ids
    task.requested_grasp_ids = None
    task.grasp_position[ids] = task.grasp_positions[task.grasp_ids[ids]]
    task.grasp_rotation[ids] = task.grasp_rotations[task.grasp_ids[ids]]


def reset(env, env_ids):
    task = env.task
    ids = torch.arange(env.num_envs, device=env.device) if env_ids is None else env_ids.long()
    selected = (
        task.pool[torch.randint(len(task.pool), (len(ids),), device=env.device)]
        if task.requested_station_ids is None
        else task.requested_station_ids
    )
    task.requested_station_ids = None
    task.station_ids[ids] = selected
    if not task.contact_protocol:
        reset_grasp_targets(task, selected, ids)
    env.sim.data.qpos[ids] = task.initial_qpos
    env.sim.data.qvel[ids] = 0
    q = task.robot.data.default_joint_pos[ids].clone()
    if task.physical_fixed:
        task.robot.write_mocap_pose_to_sim(task.mocap_poses[selected], env_ids=ids)
    else:
        q[:, task.base_joints] = task.station_poses[selected]
    task.robot.write_joint_state_to_sim(q, torch.zeros_like(q), env_ids=ids)
    task.robot.set_joint_position_target(q, env_ids=ids)
    base = task.station_poses[selected] if task.physical_fixed else q[:, task.base_joints]
    task.fixed_reference[ids] = torch.cat((base, q[:, task.lift]), -1)
    task.window_start[ids] = -1
    task.reference_position[ids] = 0
    task.reference_rotation[ids] = torch.eye(3, device=env.device)
    for name in (
        "elapsed",
        "contact_success_paid",
        "hold",
        "forces",
        "support",
        "success",
        "failed",
        "unhealthy",
        "control_failed",
        "step_collision",
        "collision_time",
        "collision_hits",
        "max_drift",
        "max_yaw_drift",
        "max_lift_drift",
        "last_action",
        "reward_components",
        "contact_bonus_paid",
        "previous_both",
        "lift_peak",
        "step_contact",
        "step_lift",
        "held_time",
    ):
        getattr(task, name)[ids] = 0
    if task.contact_protocol:
        for name in ("closing_active", "closing_peak", "closing_paid"):
            getattr(task, name)[ids] = 0
    task.first_contact_time_s[ids] = -1
    task.collision_free[ids] = True
    task.needs_baseline[ids] = True


def substep(env):
    env.task.sample_physics()
    return env.task.hold


def reward(env):
    task = env.task
    # Upstream computes rewards before its final forward(), so poses and the
    # cached forces here refer to the same last native physical substep.
    dp, dr = task.goal_error()
    distance, angle = dp.norm(dim=-1), dr.norm(dim=-1)
    if getattr(task, "contact_protocol", False):
        cfg = env.research_config["reward"]
        success_event = task.success & ~task.contact_success_paid
        task.contact_success_paid |= success_event
        near = (distance < cfg["grasp_distance_m"]) & (angle < cfg["grasp_angle_rad"])
        q = task.robot.data.joint_pos
        closing = (1 - (q[:, task.fingers[0]] - q[:, task.fingers[1]]) / task.open_grip).clamp(0, 1)
        closing_progress = stage_peak_progress(
            closing, near, task.closing_active, task.closing_peak, task.closing_paid
        )
        both = finger_contact(env)
        lift = target_lift_height(env)
        potential = (lift / env.research_config["success"]["lift_m"]).clamp(0, 1)
        # Only held progress advances the episode peak; release never resets it.
        peak = torch.where(both, torch.maximum(task.lift_peak, potential), task.lift_peak)
        lift_progress = peak - task.lift_peak
        task.lift_peak[:] = peak
        task.reward_components[:] = torch.stack((
            cfg["position"] * (task.previous_distance - distance),
            cfg["rotation"] * (task.previous_angle - angle),
            -cfg["collision"] * task.step_collision.float(),
            cfg["success"] * success_event.float(),
            cfg["close"] * closing_progress,
            cfg["grasp"] * task.step_contact,
            cfg["lift"] * lift_progress,
        ), -1)
        task.previous_distance[:] = distance
        task.previous_angle[:] = angle
        return task.reward_components.sum(-1)
    cfg = env.research_config["reward"]
    both = (task.forces > P["finger_force_min_n"]).all(-1)
    grasp = ~both & (distance < cfg["grasp_distance_m"]) & (angle < cfg["grasp_angle_rad"])
    pre = ~both & ~grasp & (distance < cfg["pregrasp_distance_m"])
    approach = ~both & ~grasp & ~pre
    opening = (
        (task.robot.data.joint_pos[:, task.fingers[0]] - task.robot.data.joint_pos[:, task.fingers[1]]) / task.open_grip
    ).clamp(0, 1)
    pose = torch.exp(-((distance / cfg["pose_distance_m"]) ** 2) - (angle / cfg["pose_angle_rad"]) ** 2)
    linear, angular = task.tcp_velocity()
    slow = torch.exp(
        -((linear.norm(dim=-1) / cfg["slow_linear_m_s"]) ** 2) - (angular.norm(dim=-1) / cfg["slow_angular_rad_s"]) ** 2
    )
    dt = env.step_dt
    task.reward_components[:] = torch.stack(
        (
            cfg["position"] * (task.previous_distance - distance),
            cfg["rotation"] * (task.previous_angle - angle),
            -cfg["collision"] * task.step_collision.float(),
            -cfg["limit"] * env.action_manager.get_term("tcp").limit_fraction,
            -cfg["control_failure"] * task.control_failed.float(),
            cfg["success"] * task.success.float(),
            cfg["open"] * dt * approach * opening,
            cfg["pose"] * dt * (pre | grasp) * pose,
            cfg["slow"] * dt * pre * pose * slow,
            cfg["close"] * dt * grasp * (1 - opening),
            cfg["contact"] * task.step_contact,
            cfg["lift"] * task.step_lift,
            cfg["held"] * both * task.held_time,
        ),
        -1,
    )
    task.previous_distance[:] = distance
    task.previous_angle[:] = angle
    return task.reward_components.sum(-1)


def terminated(env):
    return env.task.success | env.task.failed | env.task.unhealthy


def timeout(env):
    return env.episode_length_buf >= env.max_episode_length
