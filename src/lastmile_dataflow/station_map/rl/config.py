"""Resolve robot-relative station sampling into the recorded run configuration."""

from dataclasses import asdict, dataclass

from lastmile_dataflow.utils.io import read_config


@dataclass(frozen=True)
class PPOConfig:
    gamma: float = 0.999
    gae_lambda: float = 0.95
    rollout_steps: int = 32
    epochs: int = 5
    minibatch_size: int = 512
    actor_lr: float = 3e-4
    value_lr: float = 3e-4
    clip_coef: float = 0.2
    value_coef: float = 1.0
    entropy_coef: float = 0.0
    actor_max_grad_norm: float = 0.5
    value_max_grad_norm: float = 0.5
    target_kl: float = 0.02

    def __post_init__(self):
        if min(self.rollout_steps, self.epochs, self.minibatch_size) < 1:
            raise ValueError("PPO rollout_steps, epochs and minibatch_size must be positive")
        if min(self.actor_max_grad_norm, self.value_max_grad_norm, self.target_kl) <= 0:
            raise ValueError("PPO gradient limits and target_kl must be positive")


def resolve_config(config):
    if config.get("algorithm", "sac") == "ppo":
        config = {**config, "ppo": asdict(PPOConfig(**config.get("ppo", {})))}
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
