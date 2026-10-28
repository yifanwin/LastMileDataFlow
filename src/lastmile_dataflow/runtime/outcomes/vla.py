"""Dual commanded arms, same physical success thresholds; no planner tracking tests."""
import numpy as np
from .pick import PickEvidence, constraint_failure
from .evaluation import OpenEvidence

PROTOCOL = 'mobile-vla-physical-v2'


def constraint(row, initial):
    if np.linalg.norm(np.asarray(row['base'][:2])-initial['workspace_center_xy']) > initial['workspace_radius_m']+1e-6:
        return 'base_outside_workspace'
    return constraint_failure(row, initial, check_base=False, check_torso=False, check_idle=False)


class TaskEvidence:
    """Streaming per-hand pick/open evidence; `evaluate` over all rows gives the same verdict."""

    def __init__(self, task, initials, hold_s):
        self.task = task
        self.hands = {side: PickEvidence(initial, constraint_checker=constraint) if task['operation'] == 'pick'
                      else OpenEvidence(task, hold_s) for side, initial in initials.items()}

    def add(self, side, row):
        self.hands[side].add(row)

    def extend(self, samples):
        for side, rows in samples.items():
            self.hands[side].extend(rows)
        return self

    def verdict(self):
        verdicts = {side: hand.verdict() for side, hand in self.hands.items()}
        success = next((side for side, verdict in verdicts.items() if verdict['status'] == 'success'), None)
        simulation_error = next((v['reason'] for v in verdicts.values() if v['status']=='simulation_failure'), None)
        status = 'simulation_failure' if simulation_error else 'success' if success else (
            'infrastructure_error' if any(v['status']=='infrastructure_error' for v in verdicts.values()) else 'failure')
        return {'status':status, 'reason':simulation_error if simulation_error else 'physical_'+self.task['operation'] if success else
                ('invalid_physics_evidence' if status=='infrastructure_error' else 'task_not_completed'),
                'winning_hand':success if status=='success' else None, 'hands':verdicts, 'protocol':PROTOCOL,
                'torso_error_policy':'record_only', 'both_arms_commanded':len(verdicts)>1}


def evaluate(task, samples, initials, hold_s):
    return TaskEvidence(task, {side: initials[side] for side in samples}, hold_s).extend(samples).verdict()
