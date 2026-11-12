"""Explicit selection of the two supported local VLA backends."""

from ..config import read_config
from .molmobot import MolmoBotClient, load_molmobot_config
from .pi05.client import Pi05Client
from .pi05.config import load_config as load_pi05_config

BACKENDS = {"molmobot": (load_molmobot_config, MolmoBotClient), "pi05": (load_pi05_config, Pi05Client)}


def backend(path):
    return read_config(path).get("backend", "molmobot")


def load_policy_config(path):
    return BACKENDS[backend(path)][0](path)


def create_policy(path, log_path):
    return BACKENDS[backend(path)][1](path, log_path)


def check_profile(robot, operation_backend, path=None):
    if operation_backend == "curobo":
        allowed = ("position-v1", "robocasa-v1") if robot.name == "panda_omron" else ("position-v1",)
    elif operation_backend == "molmobot":
        if robot.name != "rby1m":
            raise ValueError("MolmoBot only supports RBY-1")
        allowed = ("molmobot-native-v1",)
    elif operation_backend == "pi05":
        if load_policy_config(path).embodiment != robot.name:
            raise ValueError("π0.5 embodiment does not match scene robot")
        allowed = ("robocasa-v1",) if robot.name == "panda_omron" else ("position-v1",)
    else:
        raise ValueError("unsupported operation backend: " + operation_backend)
    if robot.control_profile not in allowed:
        raise ValueError(
            "scene control profile incompatible with requested backend; construct an explicit matching scene"
        )
