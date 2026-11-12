from lastmile_dataflow.runtime.control.interface import ROBOT_NAMESPACE
from lastmile_dataflow.robots.rby1.config import RobotConfig
from lastmile_dataflow.robots.panda_omron.config import PandaOmronConfig
from lastmile_dataflow.utils.io import read_config
import mujoco
from lastmile_dataflow.scenes.loading.mjcf import load_spec
from lastmile_dataflow.runtime.control import panda_omron, panda_robocasa
from lastmile_dataflow.runtime.control.panda_omron import SCALE as PANDA_SCALE, PandaOmronAdapter
from lastmile_dataflow.runtime.control.panda_robocasa import PandaRoboCasaAdapter
from lastmile_dataflow.runtime.control.robot import RBY1Adapter, prepare_rby1_spec
from lastmile_dataflow.planning.robots.panda_omron import PlanningModel as PandaPlanningModel
from lastmile_dataflow.planning.robots.rby1m_mobile_torso import PlanningModel as RBY1PlanningModel


RBY1_PLANNING_ADAPTER = "joint_waypoints_v1"


_PROTOCOL_KEYS = {"namespace": ROBOT_NAMESPACE, "planning_adapter": RBY1_PLANNING_ADAPTER}


def robot_config(values):
    """The sole config selection boundary; existing RobotConfig remains RBY-1.

    Snapshots written before the protocol constants left the config still carry them; they must match.
    """
    values = dict(values)
    for key, fixed in _PROTOCOL_KEYS.items():
        if values.pop(key, fixed) != fixed:
            raise ValueError(f"unsupported {key}: v1 uses {fixed}")
    cls = {"rby1m": RobotConfig, "panda_omron": PandaOmronConfig}[values.get("name", "rby1m")]
    return cls(**values)


def load_robot_config(path):
    return robot_config(read_config(path, data_fields=("model_path",)))


# (robot name, control profile or None for the default) -> (adapter, spec preparation)
ROBOTS = {
    ("rby1m", None): (RBY1Adapter, prepare_rby1_spec),
    ("panda_omron", None): (PandaOmronAdapter, panda_omron.prepare_spec),
    ("panda_omron", "robocasa-v1"): (PandaRoboCasaAdapter, panda_robocasa.prepare_spec),
}


def _robot(config):
    return ROBOTS.get((config.name, config.control_profile)) or ROBOTS[(config.name, None)]


def make_robot_adapter(model, data, config):
    return _robot(config)[0](model, data, config)


def prepare_robot_spec(spec, config):
    _robot(config)[1](spec, config)


def load_robot_spec(config, scale=None):
    """Prepare and scale factory geometry once for both simulation backends."""
    source = load_spec(config.model_path)
    prepare_robot_spec(source, config)
    if scale is None:
        scale = PANDA_SCALE if config.name == "panda_omron" else 1.0
    if scale == 1.0:
        return source
    frames = [frame for frame in source.worldbody.frames if frame.frame is None]
    bodies = [body for body in source.worldbody.bodies if body.frame is None]
    scaled = source.worldbody.add_frame(scale=scale, scaling=mujoco.mjtScaling.mjSCALING_GEOMETRY)
    for frame in frames:
        frame.set_frame(scaled)
    for body in bodies:
        body.set_frame(scaled)
    return source


PLANNING_MODELS = {"rby1m": RBY1PlanningModel, "panda_omron": PandaPlanningModel}


def planning_model(sim, config, side, path, workspace):
    """Measured planning model of the simulated robot."""
    return PLANNING_MODELS[sim.robot.config.name](sim, config, side, path, workspace)
