"""Padded graph tensors shared by online observations, replay and SAC."""

from dataclasses import dataclass, fields

import torch


REWARD_COMPONENTS = (
    "position",
    "rotation",
    "collision",
    "limit",
    "control_failure",
    "success",
    "open",
    "pose",
    "slow",
    "close",
    "contact",
    "lift",
    "held",
)

NODE_DIM = 24
NODE_TYPES = ("goal", "tabletop_obstacle", "ground_obstacle", "container")
ARM_STATE_FIELDS = (
    ("arm_q", 7, 3.14),
    ("arm_qdot", 7, 3.0),
    ("tcp_position_base", 3, 1.0),
    ("tcp_rotation_columns_6d", 6, 1.0),
    ("gripper_width", 1, "model_open_grip"),
    ("gripper_velocity", 1, 0.2),
    ("grasp_position_error_base", 3, 1.0),
    ("grasp_rotation_vector_base", 3, 3.14),
    ("target_linear_velocity_base", 3, 1.0),
    ("target_angular_velocity_base", 3, 5.0),
    ("lift_height", 1, 1.0),
    ("finger_force_log1p", 2, 5.0),
    ("support", 1, 1.0),
    ("hold_time", 1, 1.0),
    ("relative_translation_from_reference", 3, 1.0),
    ("relative_rotation_from_reference_6d", 6, 1.0),
    ("remaining_time_fraction", 1, 1.0),
    ("fixed_joint_drift_from_reset", 4, 1.0),
    ("previous_reward_distance_angle", 2, 1.0),
    ("previous_action", 7, 1.0),
    ("lift_position", 1, "model_lift_range"),
    ("lift_velocity", 1, 0.05),
    ("tcp_linear_velocity_base", 3, 1.0),
    ("tcp_angular_velocity_base", 3, 1.0),
    ("contact_bonus_paid", 1, 1.0),
    ("lift_peak_progress", 1, 1.0),
)


@dataclass
class GraphObservation:
    nodes: torch.Tensor
    node_mask: torch.Tensor
    arm_task_state: torch.Tensor

    def map(self, function):
        return GraphObservation(*(function(getattr(self, f.name)) for f in fields(self)))

    def clone(self):
        return self.map(lambda value: value.detach().clone())

    def __getitem__(self, index):
        return self.map(lambda value: value[index])

    def replace(self, ids, other):
        for f in fields(self):
            getattr(self, f.name)[ids] = getattr(other, f.name)[ids]


def graph_edges(observation):
    """Remove padding; edge type is source type, with a fifth self-loop type.

    Goal is identified by its one-hot field, never by a padded node offset.
    """
    mask = observation.node_mask
    batch, _ = mask.nonzero(as_tuple=True)
    nodes = observation.nodes[mask]
    goal_mask = nodes[:, 0] == 1
    goals = goal_mask.nonzero(as_tuple=True)[0]
    # Each graph has exactly one Goal, required at the observation boundary.
    goal_by_batch = torch.empty(mask.shape[0], dtype=torch.long, device=nodes.device)
    goal_by_batch[batch[goals]] = goals
    sources = (~goal_mask).nonzero(as_tuple=True)[0]
    loops = torch.arange(len(nodes), device=nodes.device)
    edges = torch.stack((torch.cat((sources, loops)), torch.cat((goal_by_batch[batch[sources]], loops))))
    edge_types = torch.cat((nodes[sources, :4].argmax(-1), torch.full_like(loops, 4)))
    attributes = torch.nn.functional.one_hot(edge_types, 5).to(nodes.dtype)
    return nodes, edges, attributes, goal_by_batch


class GeometryObservation:
    """One node per relevant collision geom; furniture never becomes a solid box.

    Selection is fixed once for the union of candidate workspaces. The physical
    scene is unchanged. Dynamic poses and velocities are read on every call.
    """

    def __init__(self, model, target_id, anchor, radius, device):
        import mujoco
        import numpy as np

        reference = mujoco.MjData(model)
        mujoco.mj_forward(model, reference)
        target_root = int(model.body_rootid[target_id])
        ids, types = [], []
        for g in range(model.ngeom):
            b = int(model.geom_bodyid[g])
            name = model.body(b).name
            if (
                "robot/" in name
                or model.body_rootid[b] == target_root
                or not (model.geom_contype[g] or model.geom_conaffinity[g])
                or model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE
            ):
                continue
            extent = np.linalg.norm(model.geom_aabb[g, 3:])
            if np.linalg.norm(reference.geom_xpos[g, :2] - anchor[:2]) > radius + extent:
                continue
            ids.append(g)
            container = any(word in name.lower() for word in ("table", "counter", "shelf", "cabinet", "room", "wall"))
            types.append(3 if container else 2 if reference.geom_xpos[g, 2] < 0.2 else 1)
        self.ids = torch.tensor(ids, dtype=torch.long, device=device)
        self.bodies = torch.as_tensor(model.geom_bodyid[ids].copy(), dtype=torch.long, device=device)
        self.roots = torch.as_tensor(model.body_rootid[model.geom_bodyid[ids]].copy(), dtype=torch.long, device=device)
        self.offset = torch.as_tensor(model.geom_aabb[ids, :3].copy(), dtype=torch.float32, device=device)
        self.sizes = torch.as_tensor(model.geom_aabb[ids, 3:].copy() * 2, dtype=torch.float32, device=device)
        self.types = torch.nn.functional.one_hot(torch.tensor([0, *types], device=device), 4).float()
        self.target_id = target_id
        target_geoms = np.flatnonzero(model.body_rootid[model.geom_bodyid] == target_root)
        # Keep the actual target extent, including descendant geoms.
        points = []
        for g in target_geoms:
            if not (model.geom_contype[g] or model.geom_conaffinity[g]):
                continue
            center, half = model.geom_aabb[g, :3], model.geom_aabb[g, 3:]
            corners = center + np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * half
            world = corners @ reference.geom_xmat[g].reshape(3, 3).T + reference.geom_xpos[g]
            points.extend((world - reference.xpos[target_id]) @ reference.xmat[target_id].reshape(3, 3))
        points = np.asarray(points)
        self.goal_offset = torch.tensor((points.min(0) + points.max(0)) / 2, dtype=torch.float32, device=device)
        self.goal_size = torch.tensor(points.max(0) - points.min(0), dtype=torch.float32, device=device)
        self.capacity = len(ids) + 1

    def compute(self, data, base_position, base_rotation):
        batch = len(base_position)
        geom_rotation = data.geom_xmat[:, self.ids].reshape(batch, -1, 3, 3)
        goal_rotation = data.xmat[:, self.target_id].reshape(batch, 3, 3)
        goal_position = data.xpos[:, self.target_id] + torch.einsum("bij,j->bi", goal_rotation, self.goal_offset)
        geom_position = data.geom_xpos[:, self.ids] + torch.einsum("bnij,nj->bni", geom_rotation, self.offset)
        position = torch.cat((goal_position[:, None], geom_position), 1)
        rotation = torch.cat((goal_rotation[:, None], geom_rotation), 1)
        size = torch.cat((self.goal_size[None], self.sizes), 0).expand(batch, -1, -1)
        # cvel is a world-oriented spatial velocity around the root COM.
        roots = torch.cat((torch.tensor([self.target_id], device=self.ids.device), self.roots))
        bodies = torch.cat((torch.tensor([self.target_id], device=self.ids.device), self.bodies))
        angular = data.cvel[:, bodies, :3]
        linear = data.cvel[:, bodies, 3:] + torch.cross(angular, position - data.subtree_com[:, roots], dim=-1)
        local_position = torch.einsum("bji,bnj->bni", base_rotation, position - base_position[:, None])
        local_rotation = torch.einsum("bji,bnjk->bnik", base_rotation, rotation)
        rotation6 = local_rotation[..., :2].transpose(-1, -2).flatten(-2)
        extent = torch.einsum("bnij,bnj->bni", local_rotation.abs(), size / 2)
        bottom = local_position[..., 2:3] - extent[..., 2:3]
        distance = (local_position - local_position[:, :1]).norm(dim=-1, keepdim=True)
        local_linear = torch.einsum("bji,bnj->bni", base_rotation, linear)
        local_angular = torch.einsum("bji,bnj->bni", base_rotation, angular)
        return torch.cat(
            (
                self.types.expand(batch, -1, -1),
                local_position,
                size,
                rotation6,
                distance,
                bottom,
                local_linear,
                local_angular / 5,
            ),
            -1,
        )
