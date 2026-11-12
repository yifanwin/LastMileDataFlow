"""Resolve robot-relative station sampling into the recorded run configuration."""

from lastmile_dataflow.utils.io import read_config


def resolve_config(config):
    settings = config["stations"]
    robot = read_config(settings["robot_config"])
    reach = robot["arm_working_reach_m"]
    radius = robot["base_radius_m"]
    if reach <= 0 or radius <= 0:
        raise ValueError("Robot arm_working_reach_m and base_radius_m must be positive")
    return {
        **config,
        "stations": {
            **settings,
            "base_radius_m": radius,
            "radius_m": reach,
            "spacing_m": radius / 4,
        },
    }
