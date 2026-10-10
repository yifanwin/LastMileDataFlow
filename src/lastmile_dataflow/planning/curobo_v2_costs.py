"""Target-specific contact mask; full robot/environment/self checks stay enabled.

Only the target-only checker sees masked fingertip spheres. The environment and
self-collision managers always see the original, unmodified spheres.
"""
import copy
from dataclasses import dataclass


def contact_spheres(spheres, indices, allowed):
    """Clone only the target query geometry; original robot spheres stay intact."""
    result = spheres.clone()
    if allowed:
        result[..., indices, 3] = -100.
    return result


def manager_type(context):
    import torch
    from curobo._src.rollout.cost_manager.cost_manager_robot import RobotCostManager
    from curobo._src.rollout.cost_manager.cost_manager_robot_cfg import RobotCostManagerCfg
    from curobo._src.cost.cost_scene_collision import SceneCollisionCost

    class ContactManager(RobotCostManager):
        def initialize_from_config(self, config, transition_model, scene_collision_checker=None, **kwargs):
            super().initialize_from_config(config, transition_model, scene_collision_checker, **kwargs)
            self.target_cost = None
            self.head_fk = None
            if (config.head_fov_cost_enabled or config.head_fov_endpoint_check) and context.get('fov_robot') is not None:
                from curobo._src.robot.kinematics.kinematics import Kinematics
                self.head_fk = Kinematics(context['fov_robot'].kinematics, compute_spheres=False)
            self.names = transition_model.robot_model.joint_names
            if self.head_fk is not None:
                self.head_indices = [self.names.index(n) for n in self.head_fk.joint_names]
            if config.scene_collision_cfg is not None:
                cfg = copy.copy(config.scene_collision_cfg)
                cfg.scene_collision_checker = context['target_checker']
                self.target_cost = SceneCollisionCost(cfg)
            params = transition_model.robot_model.config.kinematics_config
            indices = []
            for link in context['contact_links']:
                indices.extend(params.get_sphere_index_from_link_name(link).tolist())
            self.contact_indices = indices

        def setup_batch_tensors(self, batch_size, horizon):
            super().setup_batch_tensors(batch_size, horizon)
            if self.target_cost is not None:
                self.target_cost.setup_batch_tensors(batch_size, horizon)

        def compute_costs(self, state, cost_collection=None, goal=None, **kwargs):
            result = super().compute_costs(state, cost_collection, goal, **kwargs)
            if self.target_cost is not None and not context['attached']:
                kin = copy.copy(state.cuda_robot_model_state)
                # Differentiable copy: no writes into shared FK/robot geometry.
                kin.robot_spheres = contact_spheres(state.robot_spheres, self.contact_indices, context['contact_allowed'])
                idxs = goal.idxs_env.view(-1) if goal is not None and goal.idxs_env is not None else None
                value = self.target_cost.forward(kin, idxs, trajectory_dt=state.joint_state.dt)
                result.add(value, 'target_collision')
            if self.config.scene_collision_cfg is not None:
                xy = state.joint_state.position[..., [self.names.index('base_x'), self.names.index('base_y')]]
                center = context['local_center']
                gap = torch.clamp(torch.linalg.vector_norm(xy - center, dim=-1) - context['radius'], min=0.)
                result.add(gap.unsqueeze(-1) * self.config.scene_collision_cfg.weight.reshape(-1)[0], 'workspace_circle')
            if self.head_fk is not None:
                from .head_fov import target_camera_torch
                from curobo.types import JointState
                # Rollout JointState omits names in v0.8.0; order comes from
                # its transition model, never from an assumed arm/base order.
                q = state.joint_state.position[..., self.head_indices]
                if self.config.head_fov_endpoint_check:
                    # The tag ranks seeds by pose error/time, NOT custom soft
                    # costs. Endpoint-only metrics reject invisible IK goals,
                    # so the existing finite retries can choose other seeds.
                    # This is NOT a dense interpolated-trajectory hard check.
                    js = JointState.from_position(q[:, -1:].contiguous(), joint_names=self.head_fk.joint_names)
                    local = target_camera_torch(self.head_fk, js, context)
                    depth = -local[..., 2]
                    cost = context['fov_cost']
                    invalid = ((depth <= cost.min_depth)
                               | (local[..., 0].abs() >= depth*cost.tan_half_x)
                               | (local[..., 1].abs() >= depth*cost.tan_half_y))
                    prefix = torch.zeros((*q.shape[:1], q.shape[1]-1, 1), device=q.device, dtype=q.dtype)
                    result.add(torch.cat((prefix, invalid.to(q.dtype).unsqueeze(-1)), dim=1), 'head_fov_endpoint')
                else:
                    js = JointState.from_position(q, joint_names=self.head_fk.joint_names)
                    result.add(context['fov_cost'](target_camera_torch(self.head_fk, js, context)), 'head_fov')
            return result

    @dataclass
    class ContactManagerCfg(RobotCostManagerCfg):
        head_fov_cost_enabled: bool = False
        head_fov_endpoint_check: bool = False

        def __post_init__(self):
            super().__post_init__()
            self.class_type = ContactManager

        @staticmethod
        def create(data_dict, **kwargs):
            original = RobotCostManagerCfg.create(data_dict, **kwargs)
            return ContactManagerCfg(**vars(original), head_fov_cost_enabled=data_dict.get('head_fov_cost_enabled', False),
                head_fov_endpoint_check=data_dict.get('head_fov_endpoint_check', False))

    return ContactManagerCfg


def fov_optimizer_configs():
    """Tag ONLY soft cost_cfg via the supported optimizer-dictionary API.

    The shared factory is also used for constraint/convergence managers; adding
    FOV there would incorrectly turn the safety margin into a native constraint.
    """
    from curobo._src.util_file import get_task_configs_path, load_yaml, join_path
    result = []
    for name in ('ik/lbfgs_ik.yml', 'trajopt/lbfgs_bspline_trajopt.yml'):
        config = load_yaml(join_path(get_task_configs_path(), name))
        config['rollout']['cost_cfg']['head_fov_cost_enabled'] = True
        result.append(config)
    metrics = load_yaml(join_path(get_task_configs_path(), 'metrics_base.yml'))
    metrics['rollout']['constraint_cfg']['head_fov_endpoint_check'] = True
    return (*result, metrics)
