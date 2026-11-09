"""Target-specific contact mask; full robot/environment/self checks stay enabled.

Only the target-only checker sees masked fingertip spheres. The environment and
self-collision managers always see the original, unmodified spheres.

This module loads cuRobo on import; the planner imports it only when it builds
solvers, so CPU-only execution paths never pull cuRobo in.
"""
import copy
from dataclasses import dataclass

import torch
from curobo._src.cost.cost_scene_collision import SceneCollisionCost
from curobo._src.robot.kinematics.kinematics import Kinematics
from curobo._src.rollout.cost_manager.cost_manager_robot import RobotCostManager
from curobo._src.rollout.cost_manager.cost_manager_robot_cfg import RobotCostManagerCfg
from curobo._src.util_file import get_task_configs_path, join_path, load_yaml
from curobo.types import JointState

from .head_fov import target_camera_torch


def contact_spheres(spheres, indices, allowed):
    """Clone only the target query geometry; original robot spheres stay intact."""
    result = spheres.clone()
    if allowed:
        result[..., indices, 3] = -100.
    return result


def link_sphere_indices(params, links):
    indices = []
    for link in links:
        indices.extend(params.get_sphere_index_from_link_name(link).tolist())
    return indices


class ContactManager(RobotCostManager):
    """Robot costs plus target/handle contact, workspace circle and head FOV terms.

    `config.context` is the owning planner's PlannerContext, shared by
    reference: phase and attach flags change during one query.
    """
    def initialize_from_config(self, config, transition_model, scene_collision_checker=None, **kwargs):
        super().initialize_from_config(config, transition_model, scene_collision_checker, **kwargs)
        self.context = config.context
        self.names = transition_model.robot_model.joint_names
        self.target_cost = self.handle_cost = None
        if config.scene_collision_cfg is not None:
            self.target_cost = self._scene_cost(config, self.context.target_checker)
            if self.context.handle_checker is not None:
                self.handle_cost = self._scene_cost(config, self.context.handle_checker)
        params = transition_model.robot_model.config.kinematics_config
        self.contact_indices = link_sphere_indices(params, self.context.contact_links)
        self.handle_indices = link_sphere_indices(params, self.context.handle_contact_links)
        self.head_fk = None
        if ((config.head_fov_cost_enabled or config.head_fov_endpoint_check)
                and self.context.fov_robot is not None):
            self.head_fk = Kinematics(self.context.fov_robot.kinematics, compute_spheres=False)
            self.head_indices = [self.names.index(n) for n in self.head_fk.joint_names]

    @staticmethod
    def _scene_cost(config, checker):
        cfg = copy.copy(config.scene_collision_cfg)
        cfg.scene_collision_checker = checker
        return SceneCollisionCost(cfg)

    def setup_batch_tensors(self, batch_size, horizon):
        super().setup_batch_tensors(batch_size, horizon)
        for cost in (self.target_cost, self.handle_cost):
            if cost is not None:
                cost.setup_batch_tensors(batch_size, horizon)

    def compute_costs(self, state, cost_collection=None, goal=None, **kwargs):
        result = super().compute_costs(state, cost_collection, goal, **kwargs)
        if not self.context.attached:
            idxs = goal.idxs_env.view(-1) if goal is not None and goal.idxs_env is not None else None
            if self.target_cost is not None:
                result.add(self._contact_cost(state, self.target_cost, self.contact_indices, idxs, goal),
                           'target_collision')
            if self.handle_cost is not None:
                # A pinch on a deep handle puts the palm against it; only the
                # handle is relaxed, the surrounding body stays a hard obstacle.
                result.add(self._contact_cost(state, self.handle_cost, self.handle_indices, idxs, goal),
                           'handle_contact')
        if self.config.scene_collision_cfg is not None and self.context.movable_base:
            result.add(self._workspace_cost(state), 'workspace_circle')
        if self.head_fk is not None:
            self._add_head_fov(state, result)
        if self.config.trajectory_approach_enabled and self.context.trajectory_goals is not None:
            result.add(self._approach_cost(state, goal), 'trajectory_approach')
        return result

    def _contact_cost(self, state, cost, indices, idxs, goal=None):
        kin = copy.copy(state.cuda_robot_model_state)
        # Differentiable copy: no writes into shared FK/robot geometry.
        spheres = state.robot_spheres
        if self.context.trajectory_goals is not None and indices:
            position, quaternion = self._tool_pose(state)
            goals = self._trajectory_targets(goal)
            cfg = self.context.trajectory_config
            distance = torch.linalg.vector_norm(position - goals[:, None, :3], dim=-1)
            dot = (quaternion * goals[:, None, 3:]).sum(dim=-1).abs().clamp(max=1.)
            angle = 2 * torch.acos(dot)
            window = ((distance <= cfg.pose_tolerance_m) & (angle <= cfg.pose_tolerance_rad))
            # Ball geometry cannot distinguish pad surfaces. The independent
            # MuJoCo validator enforces pad-only contact at these terminal poses.
            masked = spheres.clone()
            masked[..., indices, 3] = torch.where(window[..., None], -100., masked[..., indices, 3])
            kin.robot_spheres = masked
        else:
            kin.robot_spheres = contact_spheres(spheres, indices, self.context.contact_allowed)
        return cost.forward(kin, idxs, trajectory_dt=state.joint_state.dt)

    def _tool_pose(self, state):
        poses = state.cuda_robot_model_state.tool_poses
        index = poses.tool_frames.index(self.context.trajectory_config.tcp_link)
        return poses.position[..., index, :], poses.quaternion[..., index, :]

    def _trajectory_targets(self, goal):
        goals = self.context.trajectory_goals
        return goals if goal is None else goals[goal.idxs_link_pose.reshape(-1)]

    def _approach_cost(self, state, goal=None):
        p, quat = self._tool_pose(state)
        goals, cfg = self._trajectory_targets(goal), self.context.trajectory_config
        # Quaternions use wxyz. The third rotation column is the TCP +Z axis.
        w, x, y, z = goals[:, 3:].unbind(-1)
        axis = torch.stack((2*(x*z+w*y), 2*(y*z-w*x), 1-2*(x*x+y*y)), dim=-1)
        progress = torch.linspace(0, 1, p.shape[1], device=p.device, dtype=p.dtype)
        # Native B-spline output ends with one stationary padded interval.
        # The approach target reaches zero before that hold, avoiding a
        # conflicting nonzero depth target at an already stationary endpoint.
        progress = (progress / (1 - 1 / cfg.spline_intervals)).clamp(max=1.)
        active = progress >= 1 - cfg.approach_fraction
        depth_target = cfg.approach_distance_m * (1 - progress) / cfg.approach_fraction
        delta = goals[:, None, :3] - p
        depth = (delta * axis[:, None]).sum(-1)
        lateral = delta - depth[..., None] * axis[:, None]
        rotation = 1 - (quat * goals[:, None, 3:]).sum(-1).square()
        value = ((lateral / cfg.approach_lateral_m).square().sum(-1)
                 + ((depth - depth_target) / cfg.approach_lateral_m).square()
                 + 4 * rotation / cfg.approach_rotation_rad**2)
        return (cfg.approach_weight * value * active).unsqueeze(-1)

    def _workspace_cost(self, state):
        xy = state.joint_state.position[..., [self.names.index('base_x'), self.names.index('base_y')]]
        gap = torch.clamp(torch.linalg.vector_norm(xy - self.context.local_center, dim=-1)
                          - self.context.radius, min=0.)
        return gap.unsqueeze(-1) * self.config.scene_collision_cfg.weight.reshape(-1)[0]

    def _add_head_fov(self, state, result):
        # Rollout JointState omits names in v0.8.0; order comes from
        # its transition model, never from an assumed arm/base order.
        q = state.joint_state.position[..., self.head_indices]
        if not self.config.head_fov_endpoint_check:
            js = JointState.from_position(q, joint_names=self.head_fk.joint_names)
            result.add(self.context.fov_cost(target_camera_torch(self.head_fk, js, self.context)), 'head_fov')
            return
        # The tag ranks seeds by pose error/time, NOT custom soft
        # costs. Endpoint-only metrics reject invisible IK goals,
        # so the existing finite retries can choose other seeds.
        # This is NOT a dense interpolated-trajectory hard check.
        js = JointState.from_position(q[:, -1:].contiguous(), joint_names=self.head_fk.joint_names)
        local = target_camera_torch(self.head_fk, js, self.context)
        depth = -local[..., 2]
        cost = self.context.fov_cost
        invalid = ((depth <= cost.min_depth)
                   | (local[..., 0].abs() >= depth*cost.tan_half_x)
                   | (local[..., 1].abs() >= depth*cost.tan_half_y))
        prefix = torch.zeros((*q.shape[:1], q.shape[1]-1, 1), device=q.device, dtype=q.dtype)
        result.add(torch.cat((prefix, invalid.to(q.dtype).unsqueeze(-1)), dim=1), 'head_fov_endpoint')


@dataclass
class ContactManagerCfg(RobotCostManagerCfg):
    head_fov_cost_enabled: bool = False
    head_fov_endpoint_check: bool = False
    trajectory_approach_enabled: bool = False
    context: object = None
    # Class attribute, not a field: set on the per-planner type by manager_type().
    planner_context = None

    def __post_init__(self):
        super().__post_init__()
        self.class_type = ContactManager

    @classmethod
    def create(cls, data_dict, **kwargs):
        original = RobotCostManagerCfg.create(data_dict, **kwargs)
        return cls(**vars(original),
                   head_fov_cost_enabled=data_dict.get('head_fov_cost_enabled', False),
                   head_fov_endpoint_check=data_dict.get('head_fov_endpoint_check', False),
                   trajectory_approach_enabled=data_dict.get('trajectory_approach_enabled', False),
                   context=cls.planner_context)


def manager_type(context):
    """Cost manager config type bound to one planner context.

    v0.8.0 creates every cost config through `type.create(data_dict, ...)` and
    requires `type(cfg)` to equal the configured type, so the context travels
    on a per-planner subclass rather than through a closure.
    """
    return type('PlannerContactManagerCfg', (ContactManagerCfg,), {'planner_context': context})


def fov_optimizer_configs():
    """Tag ONLY soft cost_cfg via the supported optimizer-dictionary API.

    The shared factory is also used for constraint/convergence managers; adding
    FOV there would incorrectly turn the safety margin into a native constraint.
    """
    result = []
    for name in ('ik/lbfgs_ik.yml', 'trajopt/lbfgs_bspline_trajopt.yml'):
        config = load_yaml(join_path(get_task_configs_path(), name))
        config['rollout']['cost_cfg']['head_fov_cost_enabled'] = True
        result.append(config)
    metrics = load_yaml(join_path(get_task_configs_path(), 'metrics_base.yml'))
    metrics['rollout']['constraint_cfg']['head_fov_endpoint_check'] = True
    return (*result, metrics)


def trajectory_optimizer_config():
    """Approach is a soft TrajOpt term, never an IK or convergence constraint."""
    config = load_yaml(join_path(get_task_configs_path(), 'trajopt/lbfgs_bspline_trajopt.yml'))
    config['rollout']['cost_cfg']['trajectory_approach_enabled'] = True
    return config
