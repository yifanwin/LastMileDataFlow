"""Explicit selection of the two supported local VLA backends."""
from pathlib import Path
import json
import math
from dataclasses import dataclass

from ..config import repo_path, read_config


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
        if type(self.task_horizon) is not int or self.task_horizon < 1:
            raise ValueError('invalid task_horizon')
        for name in ('service_timeout_s', 'rpc_timeout_s', 'load_timeout_s'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError('invalid ' + name)


def load_molmobot_config(path):
    values = read_config(path, data_fields=('checkpoint', 'tokenizer_path'), repo_fields=('python',))
    if values.pop('backend', 'molmobot') != 'molmobot':
        raise ValueError('expected molmobot policy config')
    return MolmoBotConfig(**values)


def backend(path):
    return json.loads(Path(repo_path(path)).read_text()).get('backend', 'molmobot')


def load_policy_config(path):
    name = backend(path)
    if name == 'molmobot':
        return load_molmobot_config(path)
    elif name == 'pi05':
        from .pi05.config import load_config as load
    else:
        raise ValueError('unsupported policy backend: '+name)
    return load(path)


def create_policy(path, log_path):
    if backend(path) == 'pi05':
        from .pi05.client import Pi05Client
        return Pi05Client(path, log_path)
    from .molmobot import MolmoBotClient
    return MolmoBotClient(path, log_path)


def check_profile(robot, operation_backend, path=None):
    if operation_backend == 'curobo':
        allowed = ('position-v1', 'robocasa-v1') if robot.name == 'panda_omron' else ('position-v1',)
    elif operation_backend == 'molmobot':
        if robot.name != 'rby1m':
            raise ValueError('MolmoBot only supports RBY-1')
        allowed = ('molmobot-native-v1',)
    else:
        config = load_policy_config(path)
        if not hasattr(config,'embodiment') or config.embodiment != robot.name:
            raise ValueError('π0.5 embodiment does not match scene robot')
        allowed = ('robocasa-v1',) if robot.name == 'panda_omron' else ('position-v1',)
    if robot.control_profile not in allowed:
        raise ValueError('scene control profile incompatible with requested backend; construct an explicit matching scene')
