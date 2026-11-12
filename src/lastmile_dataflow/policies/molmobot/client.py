"""MolmoBot multitask client: one service process, one action per request."""

from dataclasses import dataclass
import math

from lastmile_dataflow.utils.io import read_config
from ..process import PolicyAction, PolicyProcess


@dataclass(frozen=True)
class MolmoBotConfig:
    checkpoint: str
    python: str
    embodiment: str = "rby1m"
    tokenizer_path: str | None = None
    gripper_mode: str = "model_default"
    task_horizon: int = 400
    rpc_timeout_s: float = 30.0
    load_timeout_s: float = 900.0

    def __post_init__(self):
        if self.gripper_mode not in ("model_default", "binary", "continuous"):
            raise ValueError("unknown gripper mode")
        if type(self.task_horizon) is not int or self.task_horizon < 1:
            raise ValueError("invalid task_horizon")
        for name in ("rpc_timeout_s", "load_timeout_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("invalid " + name)


def load_molmobot_config(path):
    values = read_config(path, data_fields=("checkpoint", "tokenizer_path"), repo_fields=("python",))
    if values.pop("backend", "molmobot") != "molmobot":
        raise ValueError("expected molmobot policy config")
    return MolmoBotConfig(**values)


class MolmoBotClient:
    def __init__(self, path, log_path):
        self.config = load_molmobot_config(path)
        self.process = PolicyProcess(
            self.config, path, log_path, name="MolmoBot", service_module="lastmile_dataflow.policies.molmobot.service"
        )
        self.metadata = self.process.metadata

    def action(self, observation, *, task, attempt, seed, first=False):
        reply = self.process.predict(observation, operation=task["operation"], attempt=attempt, seed=seed, first=first)
        return PolicyAction(
            reply["action"],
            reply["raw_action"],
            reply["new_chunk"],
            reply["chunk_index"],
            reply["chunk_serial"],
            reply["inference_s"],
            reply["model_prompt"],
            reply["model_chunk_state"],
            reply["processed_images"],
            reply["conditioning_points"],
            reply["gripper_binary"],
        )

    def close(self):
        self.process.close()
