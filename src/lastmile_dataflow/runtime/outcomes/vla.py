"""Dual commanded arms, same physical success thresholds; no planner tracking tests."""
import numpy as np
from .pick import constraint_failure, evaluate_pick
from .evaluation import evaluate_open

PROTOCOL = 'mobile-vla-physical-v2'


def constraint(row, initial):
    if np.linalg.norm(np.asarray(row['base'][:2])-initial['workspace_center_xy']) > initial['workspace_radius_m']+1e-6:
        return 'base_outside_workspace'
    return constraint_failure(row, initial, check_base=False, check_torso=False, check_idle=False)


def evaluate(task, samples, initials, hold_s):
    verdicts = {}
    for side in samples:
        rows = samples[side]
        if task['operation'] == 'pick':
            verdicts[side] = evaluate_pick(rows, initials[side], constraint_checker=constraint)
        else:
            verdicts[side] = evaluate_open(task, rows, hold_s)
    success = next((side for side, verdict in verdicts.items() if verdict['status'] == 'success'), None)
    simulation_error = next((v['reason'] for v in verdicts.values() if v['status']=='simulation_failure'), None)
    status = 'simulation_failure' if simulation_error else 'success' if success else (
        'infrastructure_error' if any(v['status']=='infrastructure_error' for v in verdicts.values()) else 'failure')
    return {'status':status, 'reason':simulation_error if simulation_error else 'physical_'+task['operation'] if success else
            ('invalid_physics_evidence' if status=='infrastructure_error' else 'task_not_completed'),
            'winning_hand':success if status=='success' else None, 'hands':verdicts, 'protocol':PROTOCOL,
            'torso_error_policy':'record_only', 'both_arms_commanded':len(samples)>1}
