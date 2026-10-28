"""Explicit selection of the local VLA backends and the PyRUA code agent."""
from pathlib import Path
import json

from ..config import repo_path


def backend(path):
    return json.loads(Path(repo_path(path)).read_text()).get('backend', 'molmobot')


def load_policy_config(path):
    name = backend(path)
    if name == 'molmobot':
        from .molmobot_config import load_policy_config as load
    elif name == 'pi05':
        from .pi05.config import load_config as load
    elif name == 'pyrua':
        from .pyrua.config import load_config as load
    else:
        raise ValueError('unsupported policy backend: '+name)
    return load(path)


def create_policy(path, log_path):
    if backend(path) == 'pyrua':
        from .pyrua.client import PyruaPolicy
        return PyruaPolicy(path, log_path)
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
            raise ValueError(f'{operation_backend} embodiment does not match scene robot')
        allowed = ('robocasa-v1',) if robot.name == 'panda_omron' else ('position-v1',)
    if robot.control_profile not in allowed:
        raise ValueError('scene control profile incompatible with requested backend; construct an explicit matching scene')
