"""Eight explicit geometric builders; final labels require real calibration."""
from .reachability import ReachabilityBuilder
from .ground_obstacle import GroundObstacleBuilder
from .visibility import VisibilityBuilder
from .arm_path import ArmPathCollisionBuilder
from .approach import ApproachBuilder
from .narrow_space import NarrowSpaceBuilder
from .orientation import OrientationBuilder
from .articulated import ArticulatedBuilder


BUILDERS = {
    'reachability': ReachabilityBuilder,
    'ground_obstacle': GroundObstacleBuilder,
    'visibility': VisibilityBuilder,
    'arm_path_collision': ArmPathCollisionBuilder,
    'approach': ApproachBuilder,
    'narrow_space': NarrowSpaceBuilder,
    'orientation': OrientationBuilder,
    'articulated_interaction': ArticulatedBuilder,
}


def create_builders(modes):
    return {mode: BUILDERS[mode]() for mode in modes}
