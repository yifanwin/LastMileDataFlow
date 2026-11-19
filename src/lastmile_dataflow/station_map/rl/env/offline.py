"""Fixed-Home initial observations directly from task configuration; no simulator."""

import torch

from .observation import CUBE_GRASP_ROTATION, FlatObservationState, build_flat_observation, pose_goal_error


def initial_observations(config, stations, device):
    """Transform configured Home TCP and settled cube state for each base pose."""
    settings = config["observation"]
    pose = torch.tensor([[s["x"], s["y"], s["yaw"]] for s in stations], device=device, dtype=torch.float32)
    count = len(stations)
    c, s = pose[:, 2].cos(), pose[:, 2].sin()
    rotation = torch.zeros(count, 3, 3, device=device)
    rotation[:, 0, 0], rotation[:, 0, 1] = c, -s
    rotation[:, 1, 0], rotation[:, 1, 1] = s, c
    rotation[:, 2, 2] = 1
    base = torch.cat((pose[:, :2], torch.full((count, 1), settings["base_height"], device=device)), -1)
    tcp_local = torch.tensor(settings["tcp_position_base"], device=device)
    tcp_local_rotation = torch.tensor(settings["tcp_rotation_base"], device=device)
    tcp = base + torch.einsum("bij,j->bi", rotation, tcp_local)
    tcp_rotation = rotation @ tcp_local_rotation
    cube = torch.tensor(settings["cube_position_world"], device=device).expand(count, -1)
    cube_rotation = torch.tensor(settings["cube_rotation_world"], device=device).expand(count, -1, -1)
    poses = (tcp, tcp_rotation, base, rotation, cube, cube_rotation)
    zero = torch.zeros(count, 3, device=device)
    grasp_rotation = torch.tensor(CUBE_GRASP_ROTATION, device=device).expand(count, -1, -1)
    dp, dr = pose_goal_error(poses, zero, grasp_rotation)
    state = FlatObservationState(
        poses=poses, position_error=dp, rotation_error=dr,
        arm_q=torch.tensor(config["scene"]["home"], device=device).expand(count, -1),
        lift_q=torch.full((count, 1), config["scene"]["lift"], device=device),
        arm_qdot=torch.zeros(count, 7, device=device), lift_qdot=torch.zeros(count, 1, device=device),
        cube_linear=zero, cube_angular=zero, tcp_linear=zero, tcp_angular=zero,
        gripper_width=torch.full((count, 1), settings["gripper_width"], device=device),
        gripper_velocity=torch.zeros(count, 1, device=device),
        last_action=torch.zeros(count, 7, device=device),
    )
    return build_flat_observation(
        state, cube_size=config["scene"]["cube"]["size_m"], lift_range=settings["lift_range"],
        open_grip=settings["open_grip"], world_state=settings["world_state"],
    )
