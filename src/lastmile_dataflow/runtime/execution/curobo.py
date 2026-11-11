"""One cuRobo attempt: preparation, navigation, operation and result."""
from dataclasses import asdict, dataclass, field
import logging
import os
from pathlib import Path
import time

import numpy as np

from ...data.recorder import AttemptRecorder
from ...data.rendering.analysis_video import AnalysisVideo
from ...scenes.tasks.discovery import robot_footprint
from ...utils.io import write_json
from ..control.action import InvalidAction
from ..execution_config import PLANNER_BACKEND, TORSO_ERROR_POLICY
from ..control.interface import RobotCommand
from ..outcomes.contacts import CONTACT_POLICY
from ..outcomes.task import attribute
from .checkpoint import PickEvidenceComplete, resolve_timeout, timed
from .attempt import (attempt_result, failure_status, finish_navigation, open_attempt_sim,
                      trajectory_metrics, unexpected_failure)
from .context import ContinuousContext
from .lifecycle import isolated_episode, mark_episode_phase
from .manipulation import manipulate
from .navigation import navigate
from .protocol import OperationFailure

logger = logging.getLogger(__name__)

EXECUTION_SEMANTICS = 'continuous-execution-v2'
SPLIT_EXECUTION_SEMANTICS = 'continuous-execution-v3-active-budget'
RETRY_RELEASE_TICKS = 10
RETRY_RELEASE_WIDTH_M = .08
RETRY_TARGET_MOVED_M = .01


@dataclass
class CuroboOutcome:
    navigation: dict
    status: str = 'infrastructure_error'
    reason: str = 'unfinished'
    control: dict | None = None
    attribution: dict | None = None
    retries: list = field(default_factory=list)
    verdict: dict | None = None
    execution_termination: dict | None = None
    task_outcome: dict | None = None
    exception_detail: dict | None = None

    def fail(self, ctx, reason, *, diagnostic=None, executed=True):
        self.reason = reason
        self.status = failure_status(reason, executed=executed)
        self.attribution = attribute(reason, samples=ctx.samples, collisions=ctx.collisions, diagnostic=diagnostic)
        if ctx.phase in ('navigation', 'arrival'):
            self.navigation = {'status': 'failure', 'reason': reason}


@isolated_episode
def run_curobo_attempt(baseline, task, station, candidates, assets_dir, config, collection, *, root, attempt_id,
                       seed, path=None, goal=None, winning_control=None, prepared=None, planning_client=None):
    sim = open_attempt_sim(baseline, task, station, config, prepared)
    semantics = EXECUTION_SEMANTICS if planning_client is None else SPLIT_EXECUTION_SEMANTICS
    packet = {'execution_semantics_version': semantics, 'robot': asdict(sim.robot.config),
              'collection': asdict(collection), 'task': task, 'station': station, 'protocol': asdict(config),
              'contact_policy': CONTACT_POLICY, 'seed': seed, 'path': path, 'goal': goal,
              'scene': getattr(baseline, 'scene_id', None)}
    if prepared is not None:
        packet['preparation'] = {'path': prepared.path, 'actual_base': prepared.actual_base,
                                 'requested_station': station, 'elapsed_s': prepared.elapsed_s}
    recorder = AttemptRecorder(root, packet, attempt_id=attempt_id)
    recorder.bind_simulation(sim, width=config.width, height=config.height)
    max_plans = config.max_open_plans if task['operation'] == 'open' else config.max_operation_plans
    ctx = ContinuousContext(sim, recorder, config, budget=None if planning_client is None else planning_client.budget,
                            max_plans=max_plans,
                            prepared_state=None if planning_client is None else Path(prepared.path) / 'state.npz')
    if planning_client is not None:
        planning_client.bind(ctx)
    if config.third_person_enabled:
        ctx.analysis = _analysis_video(sim, task, station, config, path, goal, recorder.path)
    write_json(recorder.path / 'initial_state.json', sim.observe_state())
    initial_target = sim.data.xpos[sim.target_id].copy()
    mark_episode_phase('execution')
    outcome = CuroboOutcome(navigation={'status': 'running' if path else 'not_requested'})
    try:
        if path:
            outcome.navigation = navigate(ctx, path, goal)
        elif config.initial_settle_s and prepared is None:
            _settle(ctx, config.initial_settle_s)
        if ctx.budget is None:
            ctx.plan_count = 0
        retries = config.operation_retries if path else 0
        _manipulate_with_retries(ctx, outcome, task, candidates, assets_dir, seed, winning_control,
                                 retries, initial_target)
    except OperationFailure as exc:
        outcome.fail(ctx, exc.reason, diagnostic=exc.diagnostic, executed=bool(recorder.steps))
    except InvalidAction as exc:
        # An illegal command is an isolated trial failure. Do not continue this
        # simulation, retry an unsafe command, or abort other independent points.
        outcome.fail(ctx, 'control_limit:' + str(exc))
        recorder.event('control_limit_warning', phase=ctx.phase, error_type='InvalidAction', message=str(exc))
        logger.warning('%s: %s；跳过当前试验，继续采样', attempt_id, outcome.reason)
    except PickEvidenceComplete as exc:
        outcome.task_outcome = exc.verdict
        outcome.status, outcome.reason, outcome.control = 'success', exc.verdict['reason'], ctx.active_control
        recorder.event('physical_pick_success', phase=ctx.phase, task_outcome=exc.verdict)
    except TimeoutError as exc:
        outcome.execution_termination, outcome.task_outcome = resolve_timeout(ctx, task, str(exc))
        task_status = outcome.task_outcome['status']
        outcome.status = task_status if task_status in ('success', 'infrastructure_error', 'simulation_failure') else 'timeout'
        outcome.reason = outcome.task_outcome['reason'] if outcome.status == 'success' else str(exc)
        outcome.control = ctx.active_control or outcome.control
        recorder.event('episode_timeout', message=str(exc), task_outcome=outcome.task_outcome)
    except Exception as exc:
        outcome.status, outcome.reason, outcome.exception_detail = unexpected_failure(exc, recorder, ctx.phase)
    finally:
        mark_episode_phase('finalize')
        result = _finalize(ctx, outcome, task, config, semantics, path, planning_client)
    return attempt_result(result, recorder, station, seed=seed,
                          planning_ref=(str(recorder.path.relative_to(recorder.output_root) / 'planning.json')
                                        if (recorder.path / 'planning.json').is_file() else None),
                          replay_complete=recorder.result['replay_complete'])


def _analysis_video(sim, task, station, config, path, goal, attempt_dir):
    footprint = robot_footprint(sim)
    write_json(attempt_dir / 'analysis_video.json', {
        'camera': 'third_person_camera',
        'style': 'observer + synchronized wrist inset + measured facts + planned/actual path',
        'station_footprint': footprint, 'station_circle_inflation_m': 0.,
        'dimensions': [1280, 720], 'playback_speed': 1., 'raw_robot_cameras_unchanged': True,
        'third_person_png': 'raw observer RGB',
        'overlays': 'actual simulation state; unmeasured contact shown as --'})
    return AnalysisVideo(task, station, path=path, goal=goal, radius_m=config.radius_m,
                         footprint_radius_m=footprint['radius_m'])


def _settle(ctx, duration_s):
    ctx.phase = 'settle'
    for _ in range(int(np.ceil(duration_s * ctx.sim.robot.config.control_hz))):
        ctx.tick(RobotCommand())


def _manipulate_with_retries(ctx, outcome, task, candidates, assets_dir, seed, winning_control, retries,
                             initial_target):
    """Operate; after a failure that left the target in place, open the grippers and retry."""
    for retry in range(retries + 1):
        try:
            outcome.verdict, outcome.control = manipulate(ctx, task, candidates, assets_dir, seed + retry,
                                                          winning_control)
            outcome.status, outcome.reason = outcome.verdict['status'], outcome.verdict['reason']
            if outcome.status == 'success':
                return
            raise OperationFailure(outcome.reason)
        except OperationFailure as exc:
            outcome.fail(ctx, exc.reason, diagnostic=exc.diagnostic, executed=bool(ctx.recorder.steps))
            outcome.retries.append({'retry': retry, 'reason': exc.reason, 'attribution': outcome.attribution})
            moved = np.linalg.norm(ctx.sim.data.xpos[ctx.sim.target_id] - initial_target) > RETRY_TARGET_MOVED_M
            if retry >= retries or outcome.status == 'simulation_failure' or moved:
                return
            _release_for_retry(ctx)


def _release_for_retry(ctx):
    ctx.monitor = None
    ctx.phase = 'retry_release'
    command = RobotCommand(gripper_width={arm: RETRY_RELEASE_WIDTH_M for arm in ctx.sim.robot.arms})
    for _ in range(RETRY_RELEASE_TICKS):
        ctx.tick(command)
    ctx.plan_count = 0


def _finalize(ctx, outcome, task, config, semantics, path, planning_client):
    """Close the context, write diagnostics and finish the recorder; returns the outcome fields."""
    sim, recorder = ctx.sim, ctx.recorder
    if path:
        outcome.navigation = finish_navigation(
            outcome.navigation, ctx.navigation_contacts, outcome.reason,
            steps=sum(phase == 'navigation' for phase in recorder.phases))
    with timed(ctx, 'context_finalize'):
        ctx.finish()
    status, reason = outcome.status, outcome.reason
    termination = outcome.execution_termination or {
        'status': 'completed' if status in ('success', 'failure', 'planning_no_solution') else status,
        'reason': reason, 'phase': ctx.phase}
    task_outcome = outcome.task_outcome
    if task_outcome is None:
        verdict = outcome.verdict
        task_outcome = verdict if verdict and verdict.get('status') == status else {
            'status': status if status in ('success', 'failure', 'simulation_failure') else 'unknown',
            'reason': reason}
    diagnostics = _diagnostics(ctx, config, semantics, planning_client)
    result = {'execution_semantics_version': semantics, 'execution_termination': termination,
              'task_outcome': task_outcome, 'execution_diagnostics': diagnostics,
              'status': status, 'reason': reason, 'attribution': outcome.attribution, 'control': outcome.control,
              'exception_detail': outcome.exception_detail, 'navigation': outcome.navigation,
              'retry_history': outcome.retries, 'continuous_simulation': True,
              'operation_base_mode': config.operation_base_mode, 'planner_backend': PLANNER_BACKEND,
              'manipulation_protocol': 'mobile-pick-v2' if task['operation'] == 'pick' else 'mobile-open-v2',
              'torso_error_policy': TORSO_ERROR_POLICY, 'contact_policy': CONTACT_POLICY,
              'trajectory_metrics': trajectory_metrics(ctx.contact_stats, sim, status=status,
                                                       task_status=task_outcome['status'])}
    finish_started = time.monotonic()
    recorder.finish(status, reason)
    diagnostics['recorder_finalize_wall_s'] = time.monotonic() - finish_started
    diagnostics['wall_including_recorder_finalize_s'] = time.monotonic() - ctx.started_at
    if ctx.budget is not None:
        diagnostics['trial_wall_s'] = time.monotonic() - ctx.budget.created_at
    write_json(recorder.path / 'execution_diagnostics.json', diagnostics)
    return result


def _diagnostics(ctx, config, semantics, planning_client):
    diagnostics = {'schema_version': semantics, 'wall_elapsed_s': time.monotonic() - ctx.started_at,
                   'attempt_wall_budget_s': config.attempt_timeout_s,
                   'simulation_elapsed_s': float(ctx.sim.data.time) - ctx.initial_sim_time,
                   'timings': ctx.timings,
                   'timing_scope': 'context lifetime; simulation includes physics monitor/trace; '
                                   'initialization excluded; recorder finalization reported separately'}
    if ctx.budget is not None:
        diagnostics.update(
            active_budget=asdict(ctx.budget), remaining_active_s=ctx.budget.remaining_s,
            execution_worker={'pid': os.getpid(), 'worker': planning_client.worker,
                              'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES')},
            timing_scope='active budget: GPU planning calls + measured physical execution; queue and I/O excluded')
    return diagnostics
