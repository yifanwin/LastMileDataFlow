"""Numerical state and trial identity crossing preparation/planning/execution."""
from dataclasses import dataclass, field
import time

import mujoco
import numpy as np


@dataclass
class SimulationState:
    integration: np.ndarray
    state_spec: int
    control: dict
    clock_remainder: float

    @classmethod
    def capture(cls, sim):
        return cls(sim.state_vector(), int(sim.state_spec),
                   sim.robot.snapshot_control(), sim._clock_remainder)

    def initialize(self, sim):
        """Only new instances may receive a prepared or measured planning state."""
        if sim.started or sim.closed:
            raise RuntimeError('state initialization requires an unstarted simulation')
        if self.state_spec != int(sim.state_spec) or self.integration.shape != sim.state_vector().shape:
            raise ValueError('state does not match simulation model')
        mujoco.mj_setState(sim.model, sim.data, self.integration, self.state_spec)
        sim.robot.restore_control(self.control)
        sim._clock_remainder = self.clock_remainder
        mujoco.mj_forward(sim.model, sim.data)


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
    limit_s: float = 300.
    planning_s: float = 0.
    execution_s: float = 0.
    queue_s: float = 0.
    plan_count: int = 0
    created_at: float = field(default_factory=time.monotonic)

    @property
    def remaining_s(self):
        return self.limit_s - self.planning_s - self.execution_s

    def check(self):
        if self.remaining_s <= 0:
            raise TimeoutError('attempt_active_budget')


@dataclass
class PlanningRequest:
    query_id: str
    model_dir: str
    identities: list[TrialIdentity]
    state: SimulationState
    stage: str
    budgets: list[TrialBudget]
    inputs: dict = field(default_factory=dict)
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
    queue_s: float = 0.
    completed_at: float = field(default_factory=time.monotonic)
