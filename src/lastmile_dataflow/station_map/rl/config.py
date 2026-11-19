"""Resolve robot-relative station sampling into the recorded run configuration."""

from dataclasses import dataclass

from lastmile_dataflow.utils.io import read_config, repo_path
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
        preprocessed = config["grasp"].get("preprocessed_path")
        defaults = ({"sample_count": DEFAULT_FPS_COUNT, "sample_seed": DEFAULT_FPS_SEED} if preprocessed
                    else {"fps_count": DEFAULT_FPS_COUNT, "fps_seed": DEFAULT_FPS_SEED})
        resolved["grasp"] = {**defaults, **config["grasp"]}
        if preprocessed:
            resolved["grasp"]["preprocessed_path"] = repo_path(resolved["grasp"]["preprocessed_path"])
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


@dataclass(frozen=True)
class BestCheckpointConfig:
    window_episodes: int = 100
    start_transitions: int = 0
    metric: str = "success_rate"
    require_full_window: bool = False

    @property
    def metric_name(self):
        return f"recent_{self.window_episodes}_episode_{self.metric}"

    def eligible(self, count, transitions):
        return (transitions >= self.start_transitions and count > 0
                and (not self.require_full_window or count == self.window_episodes))


def best_checkpoint_config(training):
    settings = training.get("best_checkpoint")
    if settings is None:
        return BestCheckpointConfig()
    selection = BestCheckpointConfig(**settings, require_full_window=True)
    if (not isinstance(selection.window_episodes, int) or selection.window_episodes <= 0
            or not isinstance(selection.start_transitions, int) or selection.start_transitions < 0):
        raise ValueError("best_checkpoint requires positive window_episodes and nonnegative start_transitions")
    if selection.metric not in ("success_rate", "return"):
        raise ValueError("best_checkpoint.metric must be success_rate or return")
    return selection
