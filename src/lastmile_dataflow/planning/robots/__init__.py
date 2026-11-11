"""Planning robot models and assets."""
from .panda_omron import PlanningModel as PandaPlanningModel
from .rby1m_mobile_torso import PlanningModel as RBY1PlanningModel

PLANNING_MODELS = {"rby1m": RBY1PlanningModel, "panda_omron": PandaPlanningModel}


def planning_model(sim, config, side, path, workspace):
    """Measured planning model of the simulated robot."""
    return PLANNING_MODELS[sim.robot.config.name](sim, config, side, path, workspace)
