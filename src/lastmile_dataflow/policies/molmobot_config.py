"""Local multitask checkpoint, independent interpreter and gripper override."""
from dataclasses import dataclass

from ..config import read_config


@dataclass(frozen=True)
class MolmoBotConfig:
    checkpoint: str
    python: str
    tokenizer_path: str | None = None
    gripper_mode: str = 'model_default'
    task_horizon: int = 400
    service_timeout_s: float = 300.
    rpc_timeout_s: float = 30.
    load_timeout_s: float = 900.

    def __post_init__(self):
        if self.gripper_mode not in ('model_default', 'binary', 'continuous'):
            raise ValueError('unknown gripper mode')
        for name in ('task_horizon',):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError('invalid ' + name)
        import math
        for name in ('service_timeout_s', 'rpc_timeout_s', 'load_timeout_s'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError('invalid ' + name)


def load_policy_config(path):
    values = read_config(path, data_fields=('checkpoint', 'tokenizer_path'), repo_fields=('python',))
    if values.pop('backend', 'molmobot') != 'molmobot':
        raise ValueError('expected molmobot policy config')
    return MolmoBotConfig(**values)
