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
            self.names = transition_model.robot_model.joint_names
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
            return result

    @dataclass
    class ContactManagerCfg(RobotCostManagerCfg):
        def __post_init__(self):
            super().__post_init__()
            self.class_type = ContactManager

        @staticmethod
        def create(data_dict, **kwargs):
            original = RobotCostManagerCfg.create(data_dict, **kwargs)
            return ContactManagerCfg(**vars(original))

    return ContactManagerCfg
