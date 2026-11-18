"""Resolve robot-relative station sampling into the recorded run configuration."""

from lastmile_dataflow.utils.io import read_config
from lastmile_dataflow.operation.grasps import DEFAULT_FPS_COUNT, DEFAULT_FPS_SEED


def resolve_config(config):
    settings = config["stations"]
    robot = read_config(settings["robot_config"])
    reach = robot["arm_working_reach_m"]
    radius = robot["base_radius_m"]
    if reach <= 0 or radius <= 0:
        raise ValueError("Robot arm_working_reach_m and base_radius_m must be positive")
    resolved = dict(config)
    if "grasp" in config:
        resolved["grasp"] = {"fps_count": DEFAULT_FPS_COUNT, "fps_seed": DEFAULT_FPS_SEED, **config["grasp"]}
    return {
        **resolved,
        "stations": {
            **settings,
            "base_radius_m": radius,
            "radius_m": reach,
            "spacing_m": radius / 4,
        },
    }


ACTION_FIELDS = ("tcp_dx", "tcp_dy", "tcp_dz", "tcp_rx", "tcp_ry", "tcp_rz", "gripper")
BASE_ACTION_FIELDS = ("base_forward", "base_side", "base_yaw")


def action_fields(action_dim):
    return ACTION_FIELDS + BASE_ACTION_FIELDS if action_dim == 10 else ACTION_FIELDS
