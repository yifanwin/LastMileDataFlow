"""Failure labels are evidence-based; navigation and task outcomes stay separate."""
import numpy as np

from .pick import PROTOCOL,rotation_error,constraint_failure,evaluate_pick


def torso_tracking_error(row):
    """Measured six-joint error, telemetry only in the no-edit v2 protocol."""
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
    if collisions or 'collision' in reason or 'nonfinger' in reason or 'penetration' in reason:
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


def evaluate_open(task, samples, hold_s):
    if len(samples) < 2:
        return {'status':'failure','reason':'open_missing_hold'}
    required=abs(task['joint_goal']-task['joint_initial'])
    start=None; best=0.
    for row in samples:
        if 'joint_value' not in row or not np.isfinite(row['joint_value']):
            return {'status':'infrastructure_error','reason':'invalid_open_evidence'}
        valid=open_progress(task,row['joint_value']) >= required-1e-3 and len(row['finger_forces_n']) == 2
        if valid:
            if start is None: start=row['time_s']
            best=max(best,row['time_s']-start)
        else:
            start=None
    tail=samples[-1]['time_s']-start if start is not None else 0.
    return {'status':'success' if tail >= hold_s-1e-6 else 'failure',
            'reason':'physical_open' if tail >= hold_s-1e-6 else 'open_joint_or_contact_hold',
            'required_progress':required,'final_progress':open_progress(task,samples[-1]['joint_value']),
            'tail_hold_s':tail,'max_hold_s':best}
