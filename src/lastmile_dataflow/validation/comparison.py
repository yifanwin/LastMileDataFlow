"""Fair C0 / C-yaw / C1 comparison. No-solution means within a frozen budget.

L1 and L2 are separate: a planner failure is never a fabricated execution failure.
"""
import math
import numpy as np
from ..io import digest

SOLVER_STATUS = {'success', 'no_solution', 'unknown', 'infrastructure_error', 'not_tested', 'budget_exhausted'}
EXECUTION_STATUS = {'success', 'failure', 'not_executed', 'unknown', 'infrastructure_error', 'budget_exhausted'}
POLICY_FIELDS = {'scene_version', 'target', 'grasp_digest', 'grasp_ids', 'arms',
                 'torso_heights', 'seed', 'num_ik_seeds', 'max_attempts', 'timeout_s', 'protocol'}


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise ValueError('incomplete comparison policy')
    if policy['arms'] != ['left', 'right'] or policy['protocol'] != 'strict-pick-v3':
        raise ValueError('both arms and strict-pick-v3 required')
    if not isinstance(policy['torso_heights'], list) or len(set(policy['torso_heights'])) < 2 or any(type(x) not in (float, int) or not math.isfinite(x) or not 0 <= x <= .738 for x in policy['torso_heights']):
        raise ValueError('torso must be enabled in every condition')
    if not isinstance(policy['grasp_ids'], list) or not policy['grasp_ids'] or any(type(x) is not int or x < 0 for x in policy['grasp_ids']) or len(set(policy['grasp_ids'])) != len(policy['grasp_ids']):
        raise ValueError('invalid grasp candidates')
    if not isinstance(policy['grasp_digest'], str) or len(policy['grasp_digest']) != 64 or any(c not in '0123456789abcdef' for c in policy['grasp_digest']):
        raise ValueError('grasp digest required')
    if any(not isinstance(policy[k], str) or not policy[k] for k in ('scene_version', 'target')):
        raise ValueError('scene and target identities required')
    for key in ('seed', 'num_ik_seeds', 'max_attempts'):
        if type(policy[key]) is not int or policy[key] < (0 if key == 'seed' else 1):
            raise ValueError('invalid budget/seed')
    if type(policy['timeout_s']) not in (int, float) or not math.isfinite(policy['timeout_s']) or policy['timeout_s'] <= 0:
        raise ValueError('finite timeout required')
    return digest(policy)


def validate_station(row, fingerprint=None):
    required = {'station_id', 'edge', 'base', 'reach', 'plan', 'execution', 'legal',
                'visible', 'facing_target', 'edge_gap_m', 'policy', 'evidence'}
    if not isinstance(row, dict) or not required <= set(row): raise ValueError('incomplete station evidence')
    if row['reach'] not in SOLVER_STATUS or row['plan'] not in SOLVER_STATUS or row['execution'] not in EXECUTION_STATUS:
        raise ValueError('unknown solver/execution status')
    if not isinstance(row['base'], list) or len(row['base']) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in row['base']):
        raise ValueError('finite base pose required')
    if any(type(row[k]) is not bool for k in ('legal', 'visible', 'facing_target')):
        raise ValueError('station checks must be explicit booleans')
    if type(row['edge_gap_m']) not in (float, int) or not math.isfinite(row['edge_gap_m']) or row['edge_gap_m'] < 0:
        raise ValueError('invalid edge gap')
    if not isinstance(row['evidence'], list) or not row['evidence'] or any(not isinstance(x, str) or not x for x in row['evidence']):
        raise ValueError('evidence references required')
    actual = validate_policy(row['policy'])
    if fingerprint is not None and actual != fingerprint: raise ValueError('unfair paired comparison')
    return actual


def compare_pair(spec, packet):
    c0, c1, yaw = packet['C0'], packet['C1'], packet['C_yaw']
    fingerprint = validate_station(c0)
    validate_station(c1, fingerprint)
    if not isinstance(yaw, list) or not yaw: raise ValueError('explicit C-yaw probes required')
    for row in yaw:
        validate_station(row, fingerprint)
        if not np.allclose(row['base'][:2], c0['base'][:2], atol=1e-8, rtol=0):
            raise ValueError('C-yaw may not translate')
    edge_rows = packet.get('start_edge', [])
    for row in edge_rows:
        validate_station(row, fingerprint)
        if row['edge'] != c0['edge']: raise ValueError('start-edge coverage mismatch')
    moved = np.linalg.norm(np.array(c1['base'][:2])-c0['base'][:2]) >= spec.thresholds['translation_min_m']
    move = 'yaw_only' if any(r['plan'] == 'success' for r in yaw) or not moved else (
        'same_edge' if c0['edge'] == c1['edge'] else 'switch_edge')
    kind = spec.case_type
    if kind in ('case1', 'case1-S'):
        kind = 'case1-S' if move == 'same_edge' else 'case1'
    result = {'status': 'unknown', 'reason': 'incomplete_evidence', 'case_type': kind,
              'move_type': move, 'attribution': spec.attribution, 'improvement_level': None,
              'construction_validity': 'unknown', 'case_condition': 'unknown', 'task_success': 'unknown',
              'policy_digest': fingerprint, 'spec_digest': spec.spec_digest,
              'finite_budget_not_impossibility': True, 'execution_protocol': 'strict-pick-v3'}
    def finish(status, reason):
        result.update(status=status, reason=reason, case_condition=status)
        return result
    if c0['plan'] == 'success' or c0['execution'] == 'success': return finish('fail', 'C0_already_succeeds')
    if move == 'yaw_only': return finish('fail', 'yaw_only_control')
    if move not in spec.move_types and not (spec.case_type == 'case1' and kind == 'case1-S'):
        return finish('fail', 'move_type_not_allowed')
    for row in (c0, c1):
        if not row['legal'] or not row['visible'] or not row['facing_target'] or row['edge_gap_m'] > spec.thresholds['edge_gap_max_m']:
            return finish('fail', 'unreasonable_or_invalid_station')
    coverage = packet.get('coverage', {})
    if coverage.get('yaw_complete') is not True or not any(np.allclose(r['base'], c0['base'], atol=1e-8, rtol=0) for r in yaw):
        return finish('unknown', 'incomplete_yaw_coverage')
    if any(r['plan'] != 'no_solution' and not (not r['legal'] and r.get('geometry_check', {}).get('status') == 'geometry_filtered') for r in yaw):
        return finish('unknown', 'yaw_unresolved')
    if c0['plan'] != 'no_solution' or c1['plan'] not in ('success', 'no_solution'):
        return finish('unknown', 'planning_unresolved')
    if c1['plan'] == 'no_solution': return finish('fail', 'C1_budget_no_solution')
    if packet.get('path', {}).get('status') != 'pass': return finish('unknown', 'ground_path_not_verified')
    count = packet.get('success_region_count')
    if type(count) is not int or count < spec.thresholds['min_success_stations']:
        return finish('unknown', 'success_region_too_small')
    if spec.case_type in ('case1', 'case1.5', 'case1-S'):
        if c0['reach'] == 'success': return finish('fail', 'not_a_reach_failure')
        if c0['reach'] != 'no_solution': return finish('unknown', 'reach_unresolved')
        if kind != 'case1-S':
            if coverage.get('start_edge_complete') is not True or not edge_rows or not any(r['station_id'] == c0['station_id'] for r in edge_rows):
                return finish('unknown', 'incomplete_start_edge_coverage')
            if any(r['reach'] == 'success' for r in edge_rows): return finish('fail', 'start_edge_is_reachable')
            if any(r['reach'] != 'no_solution' for r in edge_rows): return finish('unknown', 'start_edge_reach_unresolved')
    if spec.case_type == 'case1.5':
        standing = packet.get('standing')
        if not standing or standing.get('coverage_complete') is not True:
            return finish('unknown', 'standing_evidence_missing')
        if standing.get('A_edge') in (c0['edge'], c1['edge']) or standing.get('B_edge') != c0['edge'] or standing.get('C_edge') != c1['edge']:
            return finish('fail', 'invalid_ABC_roles')
        if standing.get('A_standable') is not False:
            return finish('fail', 'A_side_is_standable')
        clearance, required = standing.get('clearance_m'), standing.get('required_depth_m')
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (clearance, required)) or not 0 < clearance < required:
            return finish('fail', 'A_requires_positive_but_too_narrow_gap')
    if spec.case_type in ('case2', 'case3'):
        if spec.case_type == 'case3' and c0['reach'] != 'success':
            return finish('fail' if c0['reach'] == 'no_solution' else 'unknown', 'corridor_requires_reach')
        if spec.case_type == 'case2' and packet.get('handle_annotation_human_checked') is not True:
            return finish('unknown', 'handle_annotation_not_human_checked')
        cf = packet.get('counterfactual')
        if not cf: return finish('unknown', 'counterfactual_missing')
        # The transaction may change the world (case3) or grasp subset (case2), but not the budget.
        if cf.get('kind') != spec.counterfactual or cf.get('rolled_back') is not True:
            return finish('unknown', 'counterfactual_not_rolled_back')
        if cf.get('policy') != c0['policy'] or not cf.get('evidence'):
            raise ValueError('unfair/untraceable counterfactual')
        if cf.get('plan') != 'success':
            return finish('fail' if cf.get('plan') == 'no_solution' else 'unknown', 'counterfactual_not_successful')
    result.update(improvement_level='L1', construction_validity='pass')
    # Both independent physical attempts must exist. No execution remains unknown.
    def audited(row):
        attempt = row.get('strict_attempt', {})
        return (attempt.get('protocol') == 'strict-pick-v3' and
                attempt.get('audit', {}).get('valid') is True and bool(attempt.get('path')))
    control_attempt = c0.get('strict_attempt', {})
    control_no_plan = control_attempt.get('status') == 'planning_no_solution'
    if audited(c0) and audited(c1) and (c0['execution'] == 'failure' or control_no_plan) and c1['execution'] == 'success':
        result.update(improvement_level='L2', task_success='success')
    return finish('pass', 'paired_translation_improvement')
