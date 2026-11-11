"""Recorder-free semantic stepping shared by the smoke scripts."""
from functools import partial

import mujoco

from lastmile_dataflow.runtime.execution.protocol import OperationFailure
from lastmile_dataflow.runtime.outcomes.contacts import simulation_failure


def _check(sim, sample):
    failure = simulation_failure(sim)
    if failure is None and sample is not None:
        mujoco.mj_forward(sim.model, sim.data)
        sample()
    return failure


def semantic_tick(sim, command, *, sample=None, fixed_base=False):
    """Step one semantic command; raise on the first physical failure."""
    if not sim.started:
        sim.begin()
    result, failure = sim.step(command, fixed_base=fixed_base, substep_check=partial(_check, sim, sample))
    if failure:
        raise OperationFailure(failure)
    return result
