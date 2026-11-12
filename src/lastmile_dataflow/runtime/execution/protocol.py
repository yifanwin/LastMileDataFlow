"""Numerical state and trial identity crossing preparation/planning/execution."""

from dataclasses import dataclass, field
import time

import numpy as np


class OperationFailure(RuntimeError):
    """An attempt-level physical or planning failure with an outcome reason."""

    def __init__(self, reason, diagnostic=None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


@dataclass
class SimulationState:
    integration: np.ndarray
    state_spec: int
    control: dict
    clock_remainder: float

    @classmethod
    def capture(cls, sim):
        return cls(sim.state_vector(), int(sim.state_spec), sim.robot.snapshot_control(), sim.clock_remainder)

    def initialize(self, sim):
        """Only new instances may receive a prepared or measured planning state."""
        if self.state_spec != int(sim.state_spec) or self.integration.shape != sim.state_vector().shape:
            raise ValueError("state does not match simulation model")
        sim.set_state(self.integration, self.control, self.clock_remainder)


@dataclass(frozen=True)
class TrialIdentity:
    scene_id: str
    station_index: int
    trial: int
    attempt_id: str
    seed: int


@dataclass
class TrialBudget:
    """Explicit active durations survive RPCs; queue and preparation are separate."""

    limit_s: float = 300.0
    planning_s: float = 0.0
    execution_s: float = 0.0
    queue_s: float = 0.0
    plan_count: int = 0
    created_at: float = field(default_factory=time.monotonic)

    @property
    def remaining_s(self):
        return self.limit_s - self.planning_s - self.execution_s

    def check(self):
        if self.remaining_s <= 0:
            raise TimeoutError("attempt_active_budget")


@dataclass
class PlanningInputs:
    """Scene, task and execution context a GPU planning service needs for one request."""

    task: dict
    robot: object
    config: object
    root: str
    assets_dir: str
    model_dir: str | None = None
    candidates: list | None = None  # preclose grasp batch
    preparation_path: str | None = None  # prepared station state of a preclose batch
    side: str | None = None  # pose queries of a running trial
    phase: str | None = None
    attached: bool = False
    goal: object = None
    recording_step: int | None = None


@dataclass
class PlanningRequest:
    query_id: str
    model_dir: str
    identities: list[TrialIdentity]
    state: SimulationState
    stage: str
    budgets: list[TrialBudget]
    inputs: PlanningInputs | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class PlanningResponse:
    query_id: str
    identity: TrialIdentity
    status: str
    planning_s: float
    plan_count: int
    plan: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    queue_s: float = 0.0
    completed_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class StationJob:
    """Trials of one station that share one preparation and one batched grasp plan."""

    scene: dict
    index: int
    identities: list[TrialIdentity]
    budgets: list[TrialBudget]


@dataclass(frozen=True)
class ReadyTrial:
    """A planned trial waiting for (or running on) a CPU execution worker."""

    scene: dict
    index: int
    prepared: object
    response: PlanningResponse
    budget: TrialBudget
    candidates: list
