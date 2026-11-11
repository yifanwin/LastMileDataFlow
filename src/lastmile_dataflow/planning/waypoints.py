"""Planning/navigation execution boundary: explicit results and measured state."""
from dataclasses import dataclass


GRASP_APPROACH_DISTANCE_M = .08


@dataclass(frozen=True)
class PlanResult:
    status: str  # success / no_solution / fov_constraint_failed / infrastructure_error / not_tested
    joint_names: tuple
    positions: tuple
    diagnostics: dict
    source: str = "curobo"

    def __post_init__(self):
        if self.status not in ("success", "no_solution", "fov_constraint_failed", "infrastructure_error", "not_tested"):
            raise ValueError("unknown planner result")
        if self.status != "success" and self.positions:
            raise ValueError("non-success plan must not contain executable waypoints")
        if self.status == "success" and not self.positions:
            raise ValueError("success plan requires actual waypoints")
