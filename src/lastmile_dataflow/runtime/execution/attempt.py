"""Pieces shared by the cuRobo and VLA attempt runners.

Both runners own one fresh simulation per attempt, classify failures the same
way and return the same result fields; only planning versus policy differs.
"""

from dataclasses import asdict
import traceback

from ...data.rendering.cameras import ThirdPersonCamera
from ...scenes.stations.sampling import place_frozen_station
from ..outcomes.contacts import SIMULATION_FAILURES
from .context import clone_sim
from .lifecycle import classify_episode_exception, episode_phase


def open_attempt_sim(baseline, task, station, config, prepared=None):
    """Fresh simulation at the station (or the prepared station state)."""
    sim = clone_sim(baseline, target=task["target_body"], handle=task.get("handle_body"))
    if prepared is None:
        place_frozen_station(sim, station)
    else:
        prepared.state.initialize(sim)
    if config.third_person_enabled:
        sim.third_person_camera = ThirdPersonCamera()
    return sim


def failure_status(reason, *, executed=True):
    if reason in SIMULATION_FAILURES:
        return "simulation_failure"
    return "failure" if executed else "planning_no_solution"


def unexpected_failure(exc, recorder, phase):
    """Classify an unexpected exception; returns (status, reason, exception_detail)."""
    classified = classify_episode_exception(exc, episode_phase())
    recorder.event("exception", phase=phase, error_type=type(exc).__name__, message=str(exc))
    return classified["status"], classified["reason"], {**classified, "traceback": traceback.format_exc()}


def finish_navigation(navigation, contacts, reason, *, steps=None):
    """Attach measured navigation contacts; a still-running navigation becomes unknown."""
    navigation = {**navigation, **asdict(contacts)}
    if steps is not None:
        navigation["steps"] = steps
    if navigation["status"] == "running":
        navigation.update(status="unknown", reason=reason)
    return navigation


def trajectory_metrics(stats, sim, *, status, task_status):
    return stats.metrics(
        task_success=task_status == "success" if task_status in ("success", "failure") else None,
        simulation_valid=False if status == "simulation_failure" else True if sim.started else None,
    )


def attempt_result(outcome, recorder, station, **extra):
    """Final attempt row: outcome fields plus the recorder's completion facts."""
    return {
        **outcome,
        **extra,
        "status": recorder.result["status"],
        "station_id": station["station_id"],
        "attempt": str(recorder.path),
        "rollout_id": recorder.attempt_id,
        "executed_steps": recorder.steps,
        "recording_complete": recorder.result["recording_complete"],
        "observation_complete": recorder.result["observation_complete"],
    }
