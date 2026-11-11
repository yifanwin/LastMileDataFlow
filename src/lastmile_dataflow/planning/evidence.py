"""Lane-specific native solve evidence; selected IK seeds are not a full denominator."""
import numpy as np


GRASP_PHASES = ('goalset', 'pregrasp', 'grasp', 'lift')


def array(value):
    return value.detach().cpu().numpy() if hasattr(value, 'detach') else np.asarray(value)


def lane_success(value, count):
    if value is None:
        return [False] * count
    return array(value).reshape(count, -1).any(axis=1).tolist()


def solver_evidence(result, kind, phase, query_index, solve_index, count):
    success = array(result.success)
    return {'solver': kind, 'phase': phase, 'query_index': query_index,
            'solve_index': solve_index, 'success': success.tolist(),
            'returned_shape': list(success.shape), 'lane_success': lane_success(success, count),
            'ik_denominator': 'unavailable_selected_results' if kind == 'ik' else None}


def phase_evidence(queries, solves, slot):
    """Only phases reached by this lane count; later fallback goals are excluded."""
    rows = []
    eligible = True
    for query in queries:
        phase = query['phase']
        observed = [r for r in solves if r['query_index'] == query['query_index']]
        ik = [r['lane_success'][slot] for r in observed if r['solver'] == 'ik']
        trajopt = [r['lane_success'][slot] for r in observed if r['solver'] == 'trajopt']
        success = query['lane_success'][slot]
        fov_rejected = query['fov_rejected_lanes'][slot]
        component = None
        if eligible and not success:
            component = ('fov' if fov_rejected else 'ik' if ik and not any(ik)
                         else 'trajopt' if trajopt and not any(trajopt) else 'unknown')
        rows.append({'phase': phase, 'query_index': query['query_index'],
            'input_eligible': eligible, 'status': ('success' if success else 'failure') if eligible else 'not_applicable',
            'failure_component': component, 'ik_found': any(ik) if ik else None,
            'trajopt_found': any(trajopt) if trajopt else None,
            'solver_calls': [r['solve_index'] for r in observed],
            'ik_feasible_ratio': None, 'ik_denominator': 'unavailable_selected_results'})
        if phase in GRASP_PHASES:
            eligible = eligible and success
    return rows
