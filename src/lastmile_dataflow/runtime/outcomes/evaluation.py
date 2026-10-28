"""Failure labels are evidence-based; navigation and task outcomes stay separate."""
import numpy as np

from .pick import PROTOCOL, rotation_error, constraint_failure, evaluate_pick
from .contacts import SIMULATION_FAILURES


def torso_tracking_error(row):
    """Measured six-joint error, telemetry only during V2 execution."""
    torso=np.asarray(row['torso'],float)
    h=torso[1] if row['phase']=='torso_adjust' else row['command_h']
    return float(np.max(np.abs(torso-np.array([0,h,-2*h,h,0,0]))))


def mobile_constraint_failure(row,initial):
    """v2: base motion allowed, torso error recorded rather than a stop condition."""
    distance=np.linalg.norm(np.asarray(row['base'][:2])-initial['workspace_center_xy'])
    if distance > initial['workspace_radius_m']+1e-6: return 'base_outside_workspace'
    return constraint_failure(row,initial,check_base=False,check_torso=False)


def evaluate_mobile_pick(samples,initial):
    result=evaluate_pick(samples,initial,constraint_checker=mobile_constraint_failure)
    result['protocol']='mobile-pick-v2'
    result['torso_error_policy']='record_only'
    if result['status']=='success': result['reason']='mobile_pick'
    return result


def attribute(reason, *, samples=(), collisions=(), diagnostic=None):
    result = {'category':'Unknown','reason':reason,'collisions':list(collisions)}
    if reason in SIMULATION_FAILURES:
        result['category']='SimulationFailure'
    elif 'collision' in reason or 'nonfinger' in reason or 'penetration' in reason:
        result['category']='Collision'
    elif reason == 'outside_conservative_reach_bound':
        result['category']='Reachability'; result['scope']='conservative shoulder-distance bound'
    elif diagnostic and diagnostic.get('free_ik_success') is False:
        result['category']='Reachability'; result['scope']='tested grasp/torso/arm and finite IK seeds only'
    elif 'planning' in reason or 'no_solution' in reason or reason == 'fov_constraint_failed':
        result['category']='PlanningFailure'
    elif reason.startswith('control_limit:') or reason == 'torso_feedback_limit' or 'tracking' in reason or 'drift' in reason or 'protocol' in reason or 'workspace' in reason:
        result['category']='TrackingFailure'
    elif reason in ('lift_contact_hold_or_slip','open_no_grip','open_contact_lost'):
        gripping=[r for r in samples if len(r.get('finger_forces_n',{})) == 2]
        if not gripping:
            result['category']='MissedGrasp'
        elif samples and len(samples[-1].get('finger_forces_n',{})) != 2:
            result['category']='DroppedObject' if reason == 'lift_contact_hold_or_slip' else 'LostHandleContact'
        else:
            slipped=False; reference=np.asarray(gripping[0].get('relative_pose',np.eye(4)))
            for row in gripping[1:]:
                pose=np.asarray(row.get('relative_pose',np.eye(4)))
                if np.linalg.norm(pose[:3,3]-reference[:3,3]) > PROTOCOL['relative_translation_m'] or rotation_error(
                        reference[:3,:3],pose[:3,:3]) > PROTOCOL['relative_rotation_rad']:
                    slipped=True; break
            result['category']='GraspSlip' if slipped else 'InsufficientLiftOrHold'
    elif reason.startswith('open_'):
        result['category']='ArticulationFailure'
    if diagnostic:
        result['planning_diagnostic']=diagnostic
    return result


def open_progress(task, joint_value):
    direction=np.sign(task['joint_goal']-task['joint_initial'])
    return float(direction*(joint_value-task['joint_initial']))


class OpenEvidence:
    """Streaming open verdict, identical to evaluating the complete sample list."""

    def __init__(self, task, hold_s):
        self.task, self.hold_s = task, hold_s
        self.required=abs(task['joint_goal']-task['joint_initial'])
        self.count=0; self.health=None; self.invalid=False
        self.start=None; self.best=0.; self.last=None

    def extend(self, samples):
        for row in samples:
            self.add(row)
        return self

    def add(self, row):
        self.count+=1; self.last=row
        if self.health is None:
            self.health=next((issue for issue in row.get('illegal',[]) if issue in SIMULATION_FAILURES),None)
        if self.invalid:
            return
        if 'joint_value' not in row or not np.isfinite(row['joint_value']):
            self.invalid=True; return
        valid=open_progress(self.task,row['joint_value']) >= self.required-1e-3 and len(row['finger_forces_n']) == 2
        if valid:
            if self.start is None: self.start=row['time_s']
            self.best=max(self.best,row['time_s']-self.start)
        else:
            self.start=None

    def verdict(self):
        if self.health: return {'status':'simulation_failure','reason':self.health}
        if self.count < 2:
            return {'status':'failure','reason':'open_missing_hold'}
        if self.invalid:
            return {'status':'infrastructure_error','reason':'invalid_open_evidence'}
        tail=self.last['time_s']-self.start if self.start is not None else 0.
        return {'status':'success' if tail >= self.hold_s-1e-6 else 'failure',
                'reason':'physical_open' if tail >= self.hold_s-1e-6 else 'open_joint_or_contact_hold',
                'required_progress':self.required,'final_progress':open_progress(self.task,self.last['joint_value']),
                'tail_hold_s':tail,'max_hold_s':self.best}


def evaluate_open(task, samples, hold_s):
    return OpenEvidence(task, hold_s).extend(samples).verdict()
