"""Execution termination is not the physical task outcome; thresholds stay frozen."""
from contextlib import contextmanager
import time

from ..outcomes.evaluation import evaluate_mobile_pick
from ..outcomes.pick import PROTOCOL

EXECUTION_SEMANTICS = 'continuous-execution-v2'
SPLIT_EXECUTION_SEMANTICS = 'continuous-execution-v3-active-budget'


class PickEvidenceComplete(Exception):
    def __init__(self, verdict):
        super().__init__('physical_pick_success')
        self.verdict = verdict


@contextmanager
def timed(ctx, category):
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        entry = ctx.timings.setdefault(category, {'wall_s': 0., 'calls': 0})
        entry['wall_s'] += elapsed
        entry['calls'] += 1
        if category == 'simulation_and_physics_evidence' and getattr(ctx, 'budget', None) is not None:
            ctx.budget.execution_s += elapsed


def pick_verdict(ctx):
    initial = ctx.pick_initial
    if initial is None:
        return {'status': 'unknown', 'reason': 'pick_monitor_not_initialized'}
    with timed(ctx, 'task_validation'):
        return evaluate_mobile_pick(ctx.samples, initial)


def could_have_success(ctx):
    """Cheap necessary condition only; success always uses the COMPLETE evaluator."""
    initial = ctx.pick_initial
    if initial is None or not ctx.samples:
        return False
    end = ctx.samples[-1]['time_s']
    for row in reversed(ctx.samples):
        if (set(map(int, row['finger_forces_n'])) != set(initial['fingers'])
                or row['height_m'] - initial['height_m'] < PROTOCOL['lift_m']
                or row['support_contact']):
            return False
        if end - row['time_s'] >= PROTOCOL['hold_s'] - 1e-9:
            return True
    return False


def checkpoint_pick_success(ctx):
    """At completed control boundaries, stop lift/hold at actual physical success."""
    if ctx.phase in ('lift', 'lift_attached', 'lift_actual', 'hold') and could_have_success(ctx):
        verdict = pick_verdict(ctx)
        if verdict['status'] == 'success':
            raise PickEvidenceComplete(verdict)


def hold_pick(ctx, side, q, h, max_ticks=50):
    """Same maximum hold budget, but stop at the first fully validated success."""
    for index in range(max_ticks + 1):
        if could_have_success(ctx):
            verdict = pick_verdict(ctx)
            if verdict['status'] == 'success':
                return verdict
        if index == max_ticks:
            return pick_verdict(ctx)
        ctx.arm_tick(side, q, 0., h)


def resolve_timeout(ctx, task, reason):
    """Keep timeout as termination; never promote absent/invalid evidence."""
    termination = {'status': 'timeout', 'reason': reason, 'phase': ctx.phase}
    verdict = pick_verdict(ctx) if task['operation'] == 'pick' else {
        'status': 'unknown', 'reason': 'open_timeout_not_evaluated'}
    verdict = {**verdict, 'evaluation_scope': 'recorded_trace_at_termination'}
    return termination, verdict
