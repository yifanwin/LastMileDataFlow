"""Lazy S1 measurements over actual scene graph snapshots, with three-way verdicts."""
import math

import numpy as np

from ..construction.case_schema import (CheckResult, Condition, ParameterSpec,
                                         bounds, vector)
from ..scenes.graph import SceneGraph


class _Unavailable(Exception):
    def __init__(self, reason, *, missing=False):
        self.reason = reason
        self.missing = missing


def _reference(ref, nodes, bindings):
    feature = None
    if ref.startswith('$'):
        role, _, feature = ref[1:].partition('.')
        if role not in bindings:
            raise _Unavailable('unbound_role:' + role)
        ref = bindings[role]
    if not isinstance(ref, str) or ref not in nodes:
        raise _Unavailable('missing_node:' + str(ref), missing=True)
    return ref, nodes[ref], feature


def _parameter(name, parameters):
    if name not in parameters:
        raise _Unavailable('missing_parameter:' + name)
    value = parameters[name]
    return value if isinstance(value, ParameterSpec) else ParameterSpec.from_dict(value)


def _rotation(quaternion):
    w, x, y, z = quaternion
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def _position(ref, nodes, bindings):
    _, node, feature = _reference(ref, nodes, bindings)
    if feature or 'pose' not in node:
        raise _Unavailable('body_or_station_origin_required')
    return np.asarray(node['pose']['position'], dtype=float)


def _direction(arg, nodes, bindings, parameters):
    if isinstance(arg, dict):
        if 'parameter' in arg:
            parameter = _parameter(arg['parameter'], parameters)
            if parameter.value is None:
                raise _Unavailable('direction_parameter_requires_value')
            vector(parameter.value, 'direction_parameter', nonzero=True)
            return np.asarray(parameter.value, dtype=float)
        if 'vector' in arg:
            return np.asarray(arg['vector'], dtype=float)
        _, node, feature = _reference(arg['node'], nodes, bindings)
        if feature or 'pose' not in node:
            raise _Unavailable('node_orientation_required')
        return _rotation(node['pose']['quaternion_wxyz']) @ np.asarray(arg['axis_local'])
    _, node, feature = _reference(arg, nodes, bindings)
    axes = {'x_axis': [1., 0., 0.], 'y_axis': [0., 1., 0.], 'z_axis': [0., 0., 1.]}
    if feature in axes and 'pose' in node:
        return _rotation(node['pose']['quaternion_wxyz']) @ axes[feature]
    observed = node.get('directions', {}).get(feature or 'direction')
    if observed is None or observed.get('status') != 'known':
        raise _Unavailable('direction_annotation_unknown')
    return np.asarray(observed['vector_world'], dtype=float)


def evaluate_condition(condition, graph, *, bindings=None, parameters=None):
    """Evaluate a goal/invariant, not robot reachability or task completion.

    Missing concrete nodes fail even for a negated predicate. Missing bindings,
    unsupported support and unavailable annotations stay unknown.
    """
    condition = condition if isinstance(condition, Condition) else Condition.from_dict(condition)
    bindings, parameters = bindings or {}, parameters or {}
    if isinstance(graph, SceneGraph):
        nodes, edges = graph.nodes, graph.edges
        identity = {'scene_id': graph.scene_id, 'revision': graph.revision,
                    'time_s': graph.time_s, 'stage': graph.stage}
    else:
        nodes, edges = graph['nodes'], graph['edges']
        identity = {k: graph[k] for k in ('scene_id', 'revision', 'time_s', 'stage')}
    evidence = {'observation': identity}
    try:
        predicate, args = condition.predicate, condition.args
        if predicate == 'supported':
            _, node, feature = _reference(args[0], nodes, bindings)
            if feature or node.get('support_status', 'unknown') == 'unknown':
                raise _Unavailable('support_geometry_or_contact_unknown')
            value = node['support_status'] == 'pass'
            evidence['support_observations'] = node.get('support_observations', {})
        elif predicate == 'supported_by':
            left, node, feature = _reference(args[0], nodes, bindings)
            right, support, support_feature = _reference(args[1], nodes, bindings)
            if feature or support_feature:
                raise _Unavailable('object_reference_required')
            observation = node.get('support_observations', {}).get(right)
            if observation is not None and observation['status'] == 'unknown':
                raise _Unavailable(observation['reason'])
            if observation is not None:
                value = observation['value']
                evidence.update(observation.get('evidence', {}))
            elif node.get('geometry_status') != 'known' or node.get('root_motion') != 'free' or support.get('support_capability', 'unknown') == 'unknown':
                raise _Unavailable('support_geometry_or_contact_unknown')
            else:
                value = any(e['predicate'] == 'supported_by' and e['args'] == [left, right] for e in edges)
                evidence['method'] = 'no_load_bearing_contact_on_qualified_surface'
        elif predicate in ('distance_xy', 'distance_3d'):
            positions = [_position(a, nodes, bindings) for a in args]
            delta = positions[0] - positions[1]
            value = float(np.linalg.norm(delta[:2] if predicate == 'distance_xy' else delta))
            evidence.update(unit='m', anchors='model_body_origin_or_station_base_origin',
                            positions_world=[p.tolist() for p in positions])
        elif predicate == 'direction_angle':
            a, b = [_direction(arg, nodes, bindings, parameters) for arg in args]
            if not np.isfinite(a).all() or not np.isfinite(b).all() or np.linalg.norm(a)*np.linalg.norm(b) < 1e-12:
                raise _Unavailable('nonfinite_or_zero_direction')
            value = float(math.acos(float(np.clip(np.dot(a, b)/np.linalg.norm(a)/np.linalg.norm(b), -1., 1.))))
            evidence.update(unit='rad', directions_world=[a.tolist(), b.tolist()])
        else:  # inside_region checks the entire conservative footprint, not its center.
            _, node, feature = _reference(args[0], nodes, bindings)
            _, region, region_feature = _reference(args[1], nodes, bindings)
            if feature or region_feature or region.get('kind') != 'region':
                raise _Unavailable('bounded_region_required')
            points = node.get('footprint_world')
            if points is None:
                raise _Unavailable('footprint_geometry_unknown')
            local = (np.asarray(points)-region['origin']) @ np.asarray(region['axes'])
            lo_x, hi_x, lo_y, hi_y = region['bounds']
            t = condition.tolerance
            value = bool(local[:, 0].min() >= lo_x-t and local[:, 0].max() <= hi_x+t and
                         local[:, 1].min() >= lo_y-t and local[:, 1].max() <= hi_y+t)
            evidence.update(method='entire_conservative_collision_footprint', region_id=region['region_id'])
        if isinstance(value, (bool, np.bool_)):
            passed = bool(value) == condition.value
            value = bool(value)
        else:
            if condition.range is not None:
                interval = condition.range
                if isinstance(interval, dict):
                    interval = _parameter(interval['parameter'], parameters).range
                    if interval is None:
                        raise _Unavailable('range_parameter_required')
                bounds(interval, 'condition.range')
                passed = interval[0]-condition.tolerance <= value <= interval[1]+condition.tolerance
                evidence['range'] = list(interval)
            else:
                passed = ((condition.min is None or value >= condition.min-condition.tolerance) and
                          (condition.max is None or value <= condition.max+condition.tolerance))
        return CheckResult(condition.id or predicate, 'pass' if passed else 'fail',
                           condition.required, value, 'condition_met' if passed else 'condition_not_met', evidence)
    except _Unavailable as exc:
        return CheckResult(condition.id or condition.predicate, 'fail' if exc.missing else 'unknown',
                           condition.required, None, exc.reason, evidence)


def evaluate_conditions(conditions, graph, *, bindings=None, parameters=None):
    return [evaluate_condition(c, graph, bindings=bindings, parameters=parameters) for c in conditions]
