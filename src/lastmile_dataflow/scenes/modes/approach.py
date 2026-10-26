"""One blocker of measured-data pregrasp-to-grasp corridors."""
from .arm_path import ArmPathCollisionBuilder


class ApproachBuilder(ArmPathCollisionBuilder):
    mode = 'approach'
    role = 'approach_obstacle'
    maximum = 1
    approach = True
