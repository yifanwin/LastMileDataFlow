"""Tensor facts, observations and manager terms for the pick task."""

from types import SimpleNamespace

import mujoco
import numpy as np
import torch

from .observation import (
    GeometryObservation, REWARD_COMPONENTS, CONTACT_REWARD_COMPONENTS,
    CUBE_GRASP_ROTATION, FlatObservationState, build_flat_observation, pose_goal_error,
)
from .stations import prepare_stations, station_mocap_poses
from .mobile import LocalBoundary
from lastmile_dataflow.operation.grasps import (
    DEFAULT_FPS_COUNT, DEFAULT_FPS_SEED, load_grasps, load_symmetry, sample_grasps, select_grasps,
)
from lastmile_dataflow.utils.io import repo_path
from lastmile_dataflow.operation.outcomes.pick_state import RL_PICK_THRESHOLDS as P, update_hold_window
from .scene import ROBOT_ENTITY, ROBOT_PREFIX, RLSceneConfig
from lastmile_dataflow.sim.model.geometry import body_points


# Typed collision classes (logged separately) and the penalty tier each one is charged in.
COLLISION_CLASSES = (
    ("finger_outer", "small"), ("finger_pair", "small"),
    ("palm_target", "medium"), ("finger_table", "medium"),
    ("arm_target", "large"), ("robot_scene", "large"), ("self", "large"),
)
COLLISION_TIERS = ("small", "medium", "large")


def tier_penalty(active, idle, first, sustained, rearm_steps):
    """First-contact penalty after rearm_steps without contact, sustained penalty otherwise.

    active/idle are (n, tiers); idle counts policy steps since the tier was last active.
    """
    onset = active & (idle >= rearm_steps)
    penalty = (onset.float() * first + (active & ~onset).float() * sustained).sum(-1)
    idle[:] = torch.where(active, torch.zeros_like(idle), idle + 1)
    return penalty, onset


def stuck_window(illegal, move, distance, angle, oldest, cfg):
    """Mostly colliding, TCP almost still, and no distance or angle gain over the window."""
    rows = torch.arange(len(illegal), device=illegal.device)
    no_progress = ((distance[rows, oldest] - distance.amin(-1) < cfg["distance_gain_m"])
                   & (angle[rows, oldest] - angle.amin(-1) < cfg["angle_gain_rad"]))
    return ((illegal.float().mean(-1) >= cfg["collision_fraction"])
            & (move.sum(-1) < cfg["tcp_path_m"]) & no_progress)


def sensor_addresses(model, names, device):
    return torch.tensor([int(model.sensor_adr[model.sensor(name).id]) for name in names],
                        dtype=torch.long, device=device)


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
    """Collision-bottom clearance above the original tabletop; legacy tasks use center rise."""
    task = env.task
    if getattr(task, "contact_protocol", False):
        if target_position is None:
            *_, target_position, target_rotation = task.poses()
        if hasattr(task, "target_points"):
            return target_bottom_height(task, target_position, target_rotation) - task.table_height_m
        half = env.research_config["scene"]["cube"]["size_m"] / 2
        extent_z = half * target_rotation[:, 2, :].abs().sum(-1)
        return target_position[:, 2] - extent_z - task.table_height_m
    return env.sim.data.xpos[:, task.target, 2] - task.height_reference


def target_bottom_height(task, position, rotation):
    """Lowest collision vertex in world Z, including object-frame offsets."""
    return position[:, 2] + (rotation[:, 2, :] @ task.target_points.T).amin(-1)


def grasp_episode(task, index):
    if not hasattr(task, "grasps"):
        return {}
    candidate = task.grasps[int(task.grasp_ids[index])]
    record = {"grasp_source": candidate["source"], "grasp_row": candidate["row"]}
    if getattr(task, "grasp_variants", None) is not None:
        record["grasp_variant"] = task.grasp_variant_info[int(task.grasp_variant[index])]
    return record


def axis_rotations(axis, angles):
    """Rodrigues rotations about one unit axis, (len(angles), 3, 3)."""
    k = torch.tensor([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]],
                     dtype=angles.dtype, device=angles.device)
    s, c = angles.sin()[:, None, None], angles.cos()[:, None, None]
    return torch.eye(3, dtype=angles.dtype, device=angles.device) + s * k + (1 - c) * (k @ k)


def grasp_variants(symmetry, steps, device):
    """Equivalent object-local transforms of a candidate: finger swap (TCP z by pi) for every object,
    times rotation about the object's symmetry axis for axisymmetric objects."""
    angles = torch.linspace(0, 2 * np.pi, steps + 1, device=device)[:-1] if symmetry else torch.zeros(1, device=device)
    axis = symmetry[0] if symmetry else np.array([0, 0, 1.0])
    centre = torch.tensor(symmetry[1] if symmetry else np.zeros(3), dtype=torch.float32, device=device)
    about_axis = axis_rotations(axis, angles.float())
    flips = torch.stack((torch.eye(3, device=device), torch.diag(torch.tensor([-1., -1, 1], device=device))))
    info = [{"axis_rotation_rad": float(a), "finger_swap": bool(f)} for a in angles.tolist() for f in (0, 1)]
    return SimpleNamespace(axis=about_axis.repeat_interleave(2, 0), flip=flips.repeat(len(angles), 1, 1),
                           centre=centre, info=info)


def align_grasp_targets(task, ids, tcp_rotation, target_rotation):
    """Replace each episode's candidate by its equivalent variant closest to the current TCP rotation."""
    v = task.grasp_variants
    position = task.grasp_positions[task.grasp_ids[ids]]
    rotation = task.grasp_rotations[task.grasp_ids[ids]]
    local = v.axis[None] @ rotation[:, None] @ v.flip[None]                       # (n, V, 3, 3)
    world = target_rotation[ids, None] @ local
    agreement = torch.einsum("nij,nvij->nv", tcp_rotation[ids], world)            # trace(R_tcp^T R_goal)
    best = agreement.argmax(-1)
    task.grasp_variant[ids] = best
    task.grasp_rotation[ids] = local[torch.arange(len(ids)), best]
    task.grasp_position[ids] = v.centre + torch.einsum("nij,nj->ni", v.axis[best], position - v.centre)


def pregrasp_stage(task, dp, angle, cfg, *, advance):
    """Position error and potential distance to the current target: pregrasp, then grasp.

    The pregrasp keeps the grasp orientation and lies offset metres back along the approach
    axis. Adding the offset to its distance keeps the potential continuous at the latched switch.
    After the switch, ``descent_lateral_weight`` scales the error perpendicular to the approach
    axis so that drifting sideways while descending costs more than the axial remainder.
    """
    offset = cfg.get("pregrasp_offset_m")
    if not offset:
        return dp, dp.norm(dim=-1)
    pdp, _ = task.goal_error(-offset)
    if advance:
        task.pregrasp_reached |= (
            (pdp.norm(dim=-1) < cfg["pregrasp_tolerance_m"]) & (angle < cfg["pregrasp_tolerance_rad"])
        )
    reached = task.pregrasp_reached
    grasp_distance = dp.norm(dim=-1)
    weight = cfg.get("descent_lateral_weight")
    if weight:
        axis = task.approach_axis()
        axial = (dp * axis).sum(-1)
        lateral = (dp - axial[:, None] * axis).norm(dim=-1)
        grasp_distance = torch.sqrt(axial ** 2 + (weight * lateral) ** 2)
    return (torch.where(reached[:, None], dp, pdp),
            torch.where(reached, grasp_distance, pdp.norm(dim=-1) + offset))


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
        if env.research_config["success"].get("pad_contact"):
            # Grasp facts use the pad geoms only, not the finger bodies' outer faces.
            self.finger_addresses = contact_addresses(self.model, ("pad_",), "force", self.device)
        self.typed = "collision_tiers" in env.research_config["reward"]
        if self.typed:
            self.prepare_typed_collisions(n)
        self.tcp = self.model.site(ROBOT_PREFIX + "gripper0_right_grip_site").id
        self.base = self.model.site(ROBOT_PREFIX + "base_site").id
        self.arm, _ = self.robot.find_joints([f"robot0_joint{i}" for i in range(1, 8)], preserve_order=True)
        self.fingers, _ = self.robot.find_joints(
            ["gripper0_right_finger_joint1", "gripper0_right_finger_joint2"], preserve_order=True
        )
        self.physical_fixed = env.research_config["scene"].get("fixed_base", False)
        self.mobile_base = env.research_config["action"].get("mobile_base", False)
        self.boundary = LocalBoundary(
            self.model, env.prepared.target_body, env.prepared.target_position,
            env.research_config["scene"]["local_radius_m"], self.device,
        ) if self.mobile_base else None
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
        self.last_action = torch.zeros(n, 10 if self.mobile_base else 7, device=self.device)
        self.out_of_bounds = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.max_base_speed = torch.zeros(n, device=self.device)
        self.max_base_yaw_speed = torch.zeros(n, device=self.device)
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
        self.pregrasp_reached = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.dropped = torch.zeros(n, dtype=torch.bool, device=self.device)
        self.drop_event = torch.zeros_like(self.dropped)
        self.first_contact_time_s = torch.full((n,), -1.0, device=self.device, dtype=torch.float64)
        if self.contact_protocol:
            # Keep the physical tabletop fixed independently of each episode baseline.
            cube = env.research_config["scene"].get("cube")
            if cube is not None:
                self.table_height_m = float(env.prepared.target_position[2]) - cube["size_m"] / 2
            else:
                self.table_height_m = env.prepared.support_height_m
                self.prepare_target_geometry()
            self.closing_active = torch.zeros(n, dtype=torch.bool, device=self.device)
            self.closing_peak = torch.zeros(n, device=self.device)
            self.closing_paid = torch.zeros(n, device=self.device)
            if cube is None:
                self.prepare_corpus_grasps()
                return
            # The gripper's local Y axis closes across the cube's local X;
            # local Z points downward for the approach. Columns form a proper rotation.
            self.grasp_position = torch.zeros(n, 3, device=self.device)
            self.grasp_rotation = torch.tensor(
                CUBE_GRASP_ROTATION, device=self.device
            ).expand(n, -1, -1).clone()
            self.grasp_ids = torch.zeros(n, dtype=torch.long, device=self.device)
            return
        self.prepare_corpus_grasps()

    def prepare_typed_collisions(self, n):
        model, device = self.model, self.device
        cfg = self.env.research_config["reward"]
        found = lambda prefix: contact_addresses(model, (prefix,), "found", device)
        nonfinger = [model.sensor(i).name for i in range(model.nsensor)
                     if model.sensor(i).name.startswith("nonfinger_") and model.sensor(i).name.endswith("_found")]
        palm = [name for name in nonfinger if name.endswith(ROBOT_PREFIX + "gripper0_right_right_gripper_found")]
        if len(palm) != 1:
            raise ValueError("Typed collisions need exactly one palm contact sensor")
        self.palm_address = sensor_addresses(model, palm, device)
        self.arm_target_addresses = sensor_addresses(model, [x for x in nonfinger if x not in palm], device)
        self.fmesh_found = found("fmesh_")
        self.pad_found = found("pad_")
        self.fmesh_normal = contact_addresses(model, ("fmesh_",), "normal", device).tolist()
        self.fpair_address, self.self_address = found("fpair_"), found("self_collision_")
        self.table_robot_address, self.ftable_addresses = found("table_robot_"), found("ftable_")
        self.scene_addresses = found("environment_")
        if (len(self.table_robot_address) != 1 or len(self.ftable_addresses) != 2 or len(self.fmesh_normal) != 2
                or len(self.pad_found) != 2):
            raise ValueError("Typed collision sensors are missing")
        # Finger 1 sits on the TCP -x side and finger 2 on +x; inward points to the other finger.
        self.inward_sign = torch.tensor([1.0, -1.0], device=device)
        self.inner_cos = cfg["finger_inner_cos"]
        tiers = cfg["collision_tiers"]
        self.tier_first = torch.tensor([tiers[t][0] for t in COLLISION_TIERS], device=device)
        self.tier_sustained = torch.tensor([tiers[t][1] for t in COLLISION_TIERS], device=device)
        self.class_tier = [COLLISION_TIERS.index(tier) for _, tier in COLLISION_CLASSES]
        self.rearm_steps = max(1, round(cfg["collision_rearm_s"] / self.env.step_dt))
        window = round(cfg["stuck"]["window_s"] / self.env.step_dt)
        classes = len(COLLISION_CLASSES)
        self.step_classes = torch.zeros(n, classes, dtype=torch.bool, device=device)
        self.class_time = torch.zeros(n, classes, device=device)
        self.tier_idle = torch.full((n, len(COLLISION_TIERS)), self.rearm_steps, dtype=torch.long, device=device)
        self.tier_onsets = torch.zeros(n, len(COLLISION_TIERS), device=device)
        self.stuck = torch.zeros(n, dtype=torch.bool, device=device)
        self.stuck_event = torch.zeros_like(self.stuck)
        self.window_illegal = torch.zeros(n, window, dtype=torch.bool, device=device)
        self.window_move = torch.zeros(n, window, device=device)
        self.window_distance = torch.zeros(n, window, device=device)
        self.window_angle = torch.zeros(n, window, device=device)
        self.window_steps = torch.zeros(n, dtype=torch.long, device=device)
        self.previous_tcp = torch.zeros(n, 3, device=device)

    def typed_collisions(self, tcp_r):
        """Per-substep (n, classes) flags in COLLISION_CLASSES order."""
        values = self.env.sim.data.sensordata
        present = lambda addresses: (values[:, addresses] > 0).any(-1)
        count = lambda addresses: values[:, addresses].sum(-1)
        normals = torch.stack([values[:, a:a + 3] for a in self.fmesh_normal], 1)       # finger -> target
        inward = self.inward_sign[None, :, None] * tcp_r[:, None, :, 0]
        inner = (normals * inward).sum(-1) > self.inner_cos
        # While a finger's pad touches the cup, its body contacts belong to the grasp: pressing into the
        # thin convex wall pieces can flip the reported normal outward.
        pad = values[:, self.pad_found] > 0
        outer = ((values[:, self.fmesh_found] > 0) & ~inner & ~pad).any(-1)
        # "found" counts matched contacts, so contacts beyond the fingers' share belong to other links.
        arm_table = count(self.table_robot_address) > count(self.ftable_addresses)
        return torch.stack((
            outer,
            present(self.fpair_address),
            present(self.palm_address),
            present(self.ftable_addresses),
            present(self.arm_target_addresses),
            present(self.scene_addresses) | arm_table,
            count(self.self_address) > count(self.fpair_address),
        ), -1)

    def collision_penalty(self):
        active = torch.stack([self.step_classes[:, [c for c, t in enumerate(self.class_tier) if t == tier]].any(-1)
                              for tier in range(len(COLLISION_TIERS))], -1)
        penalty, onset = tier_penalty(active, self.tier_idle, self.tier_first, self.tier_sustained, self.rearm_steps)
        self.tier_onsets += onset.float()
        return penalty

    def update_stuck(self):
        cfg = self.env.research_config["reward"]
        tcp_p = self.poses()[0]
        dp, dr = self.goal_error()
        angle = dr.norm(dim=-1)
        _, distance = pregrasp_stage(self, dp, angle, cfg, advance=False)
        window = self.window_illegal.shape[1]
        rows = torch.arange(len(tcp_p), device=self.device)
        slot = self.window_steps % window
        self.window_illegal[rows, slot] = self.step_classes.any(-1)
        self.window_move[rows, slot] = torch.where(
            self.window_steps > 0, (tcp_p - self.previous_tcp).norm(dim=-1), torch.zeros_like(angle))
        self.window_distance[rows, slot] = distance
        self.window_angle[rows, slot] = angle
        self.previous_tcp[:] = tcp_p
        self.window_steps += 1
        stuck = (self.window_steps >= window) & stuck_window(
            self.window_illegal, self.window_move, self.window_distance, self.window_angle,
            (slot + 1) % window, cfg["stuck"],
        ) & ~(self.success | self.failed | self.unhealthy)
        self.stuck_event[:] = stuck
        self.stuck |= stuck
        self.failed |= stuck

    def prepare_target_geometry(self):
        data = mujoco.MjData(self.model)
        data.qpos[:] = self.initial_qpos.cpu().numpy()
        mujoco.mj_forward(self.model, data)
        points = body_points(SimpleNamespace(model=self.model, data=data), self.target)
        local = (points - data.xpos[self.target]) @ data.xmat[self.target].reshape(3, 3)
        self.target_points = torch.tensor(np.unique(local, axis=0), device=self.device, dtype=torch.float32)
        self.target_size = self.target_points.amax(0) - self.target_points.amin(0)

    def prepare_corpus_grasps(self):
        """Load the object library once and fix a shared runtime subset for all stations."""
        env, n = self.env, self.env.num_envs
        scene_config = RLSceneConfig(**env.research_config["scene"])
        grasp = env.research_config["grasp"]
        if getattr(self, "contact_protocol", False) and not grasp.get("preprocessed_path"):
            raise ValueError("Real-object contact training requires grasp.preprocessed_path")
        path = (repo_path(grasp["preprocessed_path"]) if grasp.get("preprocessed_path")
                else scene_config.asset(grasp["path"]))
        self.grasps = load_grasps(path, env.prepared.target_body)
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
        self.grasp_variants = None
        if grasp.get("align_to_tcp"):
            self.grasp_variants = grasp_variants(load_symmetry(path), grasp.get("axis_steps", 72), self.device)
            self.grasp_variant_info = self.grasp_variants.info
            self.grasp_variant = torch.zeros(n, dtype=torch.long, device=self.device)
        if grasp.get("preprocessed_path"):
            pool = sample_grasps(self.grasps, grasp.get("sample_count", DEFAULT_FPS_COUNT),
                                 seed=grasp.get("sample_seed", DEFAULT_FPS_SEED))
        else:
            pool = select_grasps(
                self.grasps, [c["pose_local"] for c in self.grasps], grasp.get("fps_count", DEFAULT_FPS_COUNT),
                grasp.get("fps_position_scale_m", 0.1), grasp.get("fps_rotation_scale_rad", np.pi / 2),
                seed=grasp.get("fps_seed", DEFAULT_FPS_SEED),
            )
        identities = {id(c): i for i, c in enumerate(self.grasps)}
        pool_ids = [identities[id(c)] for c in pool]
        self.grasp_pools = [pool_ids for _ in self.stations]
        self.grasp_pool_counts = torch.full((len(self.stations),), len(pool_ids), device=self.device)
        self.grasp_pool_table = torch.tensor([pool_ids for _ in self.stations], device=self.device)

    def poses(self):
        data = self.env.sim.data
        tcp_p = data.site_xpos[:, self.tcp]
        tcp_r = data.site_xmat[:, self.tcp].reshape(-1, 3, 3)
        base_p = data.site_xpos[:, self.base]
        base_r = data.site_xmat[:, self.base].reshape(-1, 3, 3)
        target_p = data.xpos[:, self.target]
        target_r = data.xmat[:, self.target].reshape(-1, 3, 3)
        return tcp_p, tcp_r, base_p, base_r, target_p, target_r

    def approach_axis(self):
        """Grasp approach axis (goal TCP local Z) in the base frame."""
        _, _, _, base_r, _, target_r = self.poses()
        axis = (target_r @ self.grasp_rotation)[:, :, 2]
        return torch.einsum("bji,bj->bi", base_r, axis)

    def goal_error(self, approach_offset_m=0.0):
        return pose_goal_error(self.poses(), self.grasp_position, self.grasp_rotation, approach_offset_m)

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
        if hasattr(self, "drop_event"):
            self.drop_event.zero_()
        if getattr(self, "typed", False):
            self.step_classes.zero_()
            self.stuck_event.zero_()
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

    def base_velocity(self):
        """Measured velocity at base_site, expressed in the base frame."""
        data = self.env.sim.data
        body = int(self.model.site_bodyid[self.base])
        motion = data.cvel[:, body]
        root = int(self.model.body_rootid[body])
        position = data.site_xpos[:, self.base]
        linear = motion[:, 3:] + torch.cross(motion[:, :3], position - data.subtree_com[:, root], dim=-1)
        rotation = data.site_xmat[:, self.base].reshape(-1, 3, 3)
        local = torch.einsum("bji,bj->bi", rotation, linear)
        return torch.cat((local[:, :2], motion[:, 2:3]), -1)

    def record_base_drift(self):
        delta = self.fixed_state() - self.fixed_reference
        drift = delta[:, :2].norm(dim=-1)
        yaw = torch.atan2(delta[:, 2].sin(), delta[:, 2].cos()).abs()
        self.max_drift = torch.maximum(self.max_drift, drift)
        self.max_yaw_drift = torch.maximum(self.max_yaw_drift, yaw)
        self.max_lift_drift = torch.maximum(self.max_lift_drift, delta[:, 3].abs())
        if getattr(self, "mobile_base", False):
            velocity = self.base_velocity()
            self.max_base_speed = torch.maximum(self.max_base_speed, velocity[:, :2].norm(dim=-1))
            self.max_base_yaw_speed = torch.maximum(self.max_base_yaw_speed, velocity[:, 2].abs())
            self.out_of_bounds |= self.boundary.outside(self.env.sim.data)
            return self.out_of_bounds
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
        if getattr(self, "typed", False):
            classes = self.typed_collisions(tcp_r)
            self.step_classes |= classes
            self.class_time += classes.float() * self.env.physics_dt
            collision = classes.any(-1)
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
            # The target fell off its support (dropped or knocked down): a true terminal failure.
            dropped = (lift < -cfg.get("drop_height_m", 0.3)) & ~self.failed & ~self.success
            if hasattr(self, "drop_event"):
                self.drop_event |= dropped
                self.dropped |= dropped
            self.failed |= fixed_failure | dropped
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
            if getattr(self, "mobile_base", False):
                self.success &= ~self.out_of_bounds
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
        if getattr(self, "grasp_variants", None) is not None and self.needs_baseline.any():
            # The first observation after reset sees the actual start TCP; the choice is fixed per episode.
            align_grasp_targets(self, self.needs_baseline.nonzero(as_tuple=True)[0], tcp_r, cube_r)
        dp, dr = self.goal_error()
        angle = dr.norm(dim=-1)
        dp, distance = pregrasp_stage(self, dp, angle, self.env.research_config["reward"], advance=False)
        self.previous_distance[self.needs_baseline] = distance[self.needs_baseline]
        self.previous_angle[self.needs_baseline] = angle[self.needs_baseline]
        self.height_reference[self.needs_baseline] = cube_p[self.needs_baseline, 2]
        self.needs_baseline.zero_()
        q, v = self.robot.data.joint_pos, self.robot.data.joint_vel
        linear, angular = self.tcp_velocity()
        cube_linear, cube_angular = self.target_velocity()
        state = FlatObservationState(
            poses=(tcp_p, tcp_r, base_p, base_r, cube_p, cube_r),
            position_error=dp, rotation_error=dr,
            arm_q=q[:, self.arm], lift_q=q[:, self.lift],
            arm_qdot=v[:, self.arm], lift_qdot=v[:, self.lift],
            cube_linear=cube_linear, cube_angular=cube_angular,
            tcp_linear=linear, tcp_angular=angular,
            gripper_width=(q[:, self.fingers[0]] - q[:, self.fingers[1]])[:, None],
            gripper_velocity=(v[:, self.fingers[0]] - v[:, self.fingers[1]])[:, None],
            last_action=self.last_action,
            base_velocity=(self.base_velocity() / torch.tensor([
                self.env.research_config["action"]["base_translation_m_s"],
                self.env.research_config["action"]["base_translation_m_s"],
                self.env.research_config["action"]["base_yaw_rad_s"],
            ], device=self.device)) if getattr(self, "mobile_base", False) else None,
        )
        geometry = (
            {"target_size": self.target_size, "target_bottom_height": target_bottom_height(self, cube_p, cube_r)}
            if hasattr(self, "target_points")
            else {"cube_size": self.env.research_config["scene"]["cube"]["size_m"]}
        )
        return build_flat_observation(
            state, **geometry,
            lift_range=self.lift_range, open_grip=self.open_grip,
            world_state=self.env.research_config.get("observation", {}).get("world_state", False),
        )

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
    if hasattr(task, "grasp_pool_table"):
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
        "out_of_bounds",
        "max_base_speed",
        "max_base_yaw_speed",
        "reward_components",
        "contact_bonus_paid",
        "previous_both",
        "lift_peak",
        "step_contact",
        "step_lift",
        "held_time",
        "pregrasp_reached",
    ):
        getattr(task, name)[ids] = 0
    if hasattr(task, "drop_event"):
        task.dropped[ids] = False
        task.drop_event[ids] = False
    if task.contact_protocol:
        for name in ("closing_active", "closing_peak", "closing_paid"):
            getattr(task, name)[ids] = 0
    task.first_contact_time_s[ids] = -1
    task.collision_free[ids] = True
    task.needs_baseline[ids] = True
    if getattr(task, "typed", False):
        for name in ("step_classes", "class_time", "tier_onsets", "stuck", "stuck_event", "window_illegal",
                     "window_move", "window_distance", "window_angle", "window_steps"):
            getattr(task, name)[ids] = 0
        task.tier_idle[ids] = task.rearm_steps


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
        # Closing may use a stricter alignment gate than the grasp event.
        aligned = ((distance < cfg.get("closing_distance_m", cfg["grasp_distance_m"]))
                   & (angle < cfg.get("closing_angle_rad", cfg["grasp_angle_rad"])))
        _, distance = pregrasp_stage(task, dp, angle, cfg, advance=True)
        q = task.robot.data.joint_pos
        closing = (1 - (q[:, task.fingers[0]] - q[:, task.fingers[1]]) / task.open_grip).clamp(0, 1)
        # Optionally pay closing only once a finger presses the target, not for empty closing.
        touched = (task.forces > env.research_config["success"]["finger_force_min_n"]).any(-1)
        closing_stage = aligned & touched if cfg.get("closing_requires_contact") else aligned
        closing_progress = stage_peak_progress(
            closing, closing_stage, task.closing_active, task.closing_peak, task.closing_paid
        )
        both = finger_contact(env)
        lift = target_lift_height(env)
        potential = (lift / env.research_config["success"]["lift_m"]).clamp(0, 1)
        # Only held progress advances the episode peak; release never resets it.
        peak = torch.where(both, torch.maximum(task.lift_peak, potential), task.lift_peak)
        lift_progress = peak - task.lift_peak
        task.lift_peak[:] = peak
        # Terminal penalties (stuck, dropped target) are charged in the collision component.
        drop = cfg.get("drop_penalty", 0.0) * getattr(task, "drop_event", torch.zeros_like(task.success)).float()
        task.reward_components[:] = torch.stack((
            cfg["position"] * (task.previous_distance - distance),
            cfg["rotation"] * (task.previous_angle - angle),
            -(task.collision_penalty() + cfg["stuck"]["penalty"] * task.stuck_event.float() + drop)
            if getattr(task, "typed", False) else -cfg["collision"] * task.step_collision.float() - drop,
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
    # Termination runs before reward in each step, so the stuck event is paid in the same step.
    if getattr(env.task, "typed", False):
        env.task.update_stuck()
    return env.task.success | env.task.failed | env.task.unhealthy


def timeout(env):
    return env.episode_length_buf >= env.max_episode_length

def collision_episode(task, index):
    if not getattr(task, "typed", False):
        return {}
    return {
        "stuck": bool(task.stuck[index]),
        "dropped": bool(task.dropped[index]),
        "collision_class_s": dict(zip((name for name, _ in COLLISION_CLASSES), task.class_time[index].tolist())),
        "collision_onsets": dict(zip(COLLISION_TIERS, task.tier_onsets[index].int().tolist())),
    }


def mobile_episode(task, index):
    if not getattr(task, "mobile_base", False):
        return {}
    return {
        "out_of_bounds": bool(task.out_of_bounds[index]),
        "max_base_speed_m_s": float(task.max_base_speed[index]),
        "max_base_yaw_speed_rad_s": float(task.max_base_yaw_speed[index]),
        "base_xyyaw_world": task.fixed_state()[index, :3].cpu().tolist(),
        "base_xyyaw_initial": task.fixed_reference[index, :3].cpu().tolist(),
        "base_velocity_body": task.base_velocity()[index].cpu().tolist(),
    }
