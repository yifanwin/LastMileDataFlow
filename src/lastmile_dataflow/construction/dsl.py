"""S0 symbolic/concrete edit contracts; SI units and relative axis-angle rotation."""
import copy
from dataclasses import dataclass, field
import math

from .case_schema import (VERSION, JsonContract, Condition, CaseTemplate, CheckResult,
                          bounds, identifier, nested_list, number, references,
                          require, strings, text, vector)


OPERATIONS = ('move', 'rotate', 'add', 'remove')


def keys(value, allowed, required, path):
    require(isinstance(value, dict), path, 'object required')
    require(set(required) <= set(value) <= set(allowed), path, 'missing or unknown fields')


def rotation(value, path):
    keys(value, {'axis', 'frame', 'angle', 'pivot'}, {'axis', 'angle'}, path)
    vector(value['axis'], path + '.axis', nonzero=True)
    require(value.get('frame', 'world') in ('world', 'object'), path + '.frame', 'world or object required')
    require(value.get('pivot', 'object_origin') == 'object_origin', path + '.pivot', 'only object_origin implemented')
    angle = value['angle']
    keys(angle, {'value', 'range'}, (), path + '.angle')
    require(len(angle) == 1, path + '.angle', 'one value or range required; angles are relative radians')
    if 'range' in angle:
        bounds(angle['range'], path + '.angle.range')
    else:
        number(angle['value'], path + '.angle.value')


def search_space(value, path):
    keys(value, {'support', 'region', 'xy', 'rotation', 'frame', 'offset'}, (), path)
    require(bool(value), path, 'nonempty search space required')
    for name in ('support', 'frame'):
        if name in value:
            text(value[name], path + '.' + name)
    if 'frame' in value:
        require(value['frame'] == 'world', path + '.frame', 'only explicit world supported')
        require('support' not in value and not isinstance(value.get('region'), str),
                path + '.frame', 'support/region xy are region-local; omit explicit world frame')
    if 'region' in value:
        region = value['region']
        if isinstance(region, str):
            text(region, path + '.region')
        else:
            keys(region, {'relative_to', 'distance'}, {'relative_to', 'distance'}, path + '.region')
            text(region['relative_to'], path + '.region.relative_to')
            bounds(region['distance'], path + '.region.distance')
    if 'xy' in value:
        xy = value['xy']
        keys(xy, {'mode', 'margin', 'steps', 'x', 'y'}, {'mode'}, path + '.xy')
        require(xy['mode'] in ('candidate', 'uniform', 'grid', 'sample_inside'), path + '.xy.mode', 'unknown sampler')
        if 'margin' in xy:
            number(xy['margin'], path + '.xy.margin')
            require(xy['margin'] >= 0, path + '.xy.margin', 'negative margin')
        if 'steps' in xy:
            require(type(xy['steps']) is int and xy['steps'] > 0, path + '.xy.steps', 'positive integer required')
        for axis in ('x', 'y'):
            if axis in xy:
                bounds(xy[axis], path + '.xy.' + axis)
    if 'rotation' in value:
        rotation(value['rotation'], path + '.rotation')
    if 'offset' in value:
        keys(value['offset'], {'x', 'y', 'z'}, (), path + '.offset')
        for name, item in value['offset'].items():
            bounds(item, path + '.offset.' + name) if isinstance(item, list) else number(item, path + '.offset.' + name)


@dataclass(frozen=True)
class SymbolicDSL(JsonContract):
    proposal_id: str
    bindings: dict
    operations: list
    goals: list = field(default_factory=list)
    invariants: list = field(default_factory=list)
    sampling: dict = field(default_factory=lambda: {'selection': 'coverage', 'max_samples': 16})
    description: str = ''
    dsl_version: str = VERSION

    def __post_init__(self):
        require(self.dsl_version == VERSION, 'dsl_version', 'unsupported version')
        text(self.proposal_id, 'proposal_id')
        require(isinstance(self.bindings, dict), 'bindings', 'object required')
        for name, instance in self.bindings.items():
            identifier(name, 'bindings')
            text(instance, 'bindings.' + name)
        require(isinstance(self.operations, list) and bool(self.operations), 'operations', 'nonempty array required')
        defined = set(self.bindings)
        for i, operation in enumerate(self.operations):
            path = f'operations[{i}]'
            require(isinstance(operation, dict) and operation.get('op') in OPERATIONS, path + '.op', 'unsupported operation')
            op = operation['op']
            if op == 'move':
                keys(operation, {'op', 'subject', 'search_space', 'carry_supported'}, {'op', 'subject', 'search_space'}, path)
                search_space(operation['search_space'], path + '.search_space')
                require(type(operation.get('carry_supported', False)) is bool, path + '.carry_supported', 'boolean required')
            elif op == 'rotate':
                keys(operation, {'op', 'subject', 'axis', 'frame', 'angle', 'pivot'}, {'op', 'subject', 'axis', 'angle'}, path)
                rotation({k: v for k, v in operation.items() if k not in ('op', 'subject')}, path)
            elif op == 'remove':
                keys(operation, {'op', 'subject'}, {'op', 'subject'}, path)
            else:
                keys(operation, {'op', 'bind_as', 'asset_selector', 'search_space'}, {'op', 'bind_as', 'asset_selector', 'search_space'}, path)
                name = operation['bind_as']
                require(isinstance(name, str) and name.startswith('$'), path + '.bind_as', '$role required')
                identifier(name[1:], path + '.bind_as')
                require(name[1:] not in defined, path + '.bind_as', 'duplicate role declaration')
                selector = operation['asset_selector']
                keys(selector, {'asset_id', 'type', 'category', 'size'}, (), path + '.asset_selector')
                require(bool(selector), path + '.asset_selector', 'nonempty asset selector required')
                for key in ('asset_id', 'type'):
                    if key in selector:
                        text(selector[key], path + '.asset_selector.' + key)
                if 'category' in selector:
                    strings(selector['category'], path + '.asset_selector.category')
                if 'size' in selector:
                    keys(selector['size'], {'height', 'width', 'depth'}, (), path + '.asset_selector.size')
                    for key, value in selector['size'].items():
                        bounds(value, path + '.asset_selector.size.' + key)
                        require(value[0] > 0, path + '.asset_selector.size.' + key, 'positive size required')
                search_space(operation['search_space'], path + '.search_space')
            # An add role becomes available only after its placement has been resolved.
            used = {k: v for k, v in operation.items() if k != 'bind_as'}
            require(set(references(used)) <= defined, path, 'undeclared or forward role reference')
            if 'subject' in operation:
                subject = operation['subject']
                require(isinstance(subject, str) and subject.startswith('$') and subject[1:] in defined,
                        path + '.subject', 'bound role required')
            if op == 'add':
                defined.add(name[1:])
        for name in ('goals', 'invariants'):
            object.__setattr__(self, name, nested_list(Condition, getattr(self, name), name))
            for condition in getattr(self, name):
                require(set(references(condition.args)) <= defined, name, 'undeclared role reference')
        keys(self.sampling, {'selection', 'max_samples'}, (), 'sampling')
        require(self.sampling.get('selection', 'coverage') in ('coverage', 'random', 'grid'), 'sampling.selection', 'unknown selection')
        require(type(self.sampling.get('max_samples', 16)) is int and self.sampling.get('max_samples', 16) > 0,
                'sampling.max_samples', 'positive integer required')
        require(isinstance(self.description, str), 'description', 'string required')

    def effective_requirements(self, template):
        template = template if isinstance(template, CaseTemplate) else CaseTemplate.from_dict(template)
        return copy.deepcopy(template.requirements + self.goals)

    def effective_invariants(self, template):
        template = template if isinstance(template, CaseTemplate) else CaseTemplate.from_dict(template)
        return copy.deepcopy(template.invariants + self.invariants)

    def semantic_conflicts(self, template):
        """Endpoint contradictions are case failures, not permission denials."""
        template = template if isinstance(template, CaseTemplate) else CaseTemplate.from_dict(template)
        added = {o['bind_as'][1:] for o in self.operations if o['op'] == 'add'}
        removed = {o['subject'][1:] for o in self.operations if o['op'] == 'remove'}
        removed_ids = {self.bindings[r] for r in removed if r in self.bindings}
        removed |= {r for r, instance in self.bindings.items() if instance in removed_ids}
        issues = []
        for name, role in template.roles.items():
            if role.required and (name not in set(self.bindings) | added or name in removed):
                issues.append(CheckResult('role:' + name, 'fail', reason='required_role_missing_after'))
        for condition in self.effective_invariants(template):
            refs = set(references(condition.args))
            if refs & added:
                issues.append(CheckResult(condition.id or condition.predicate, 'fail', reason='invariant_subject_missing_before'))
            if refs & removed:
                issues.append(CheckResult(condition.id or condition.predicate, 'fail', reason='invariant_subject_missing_after'))
        for condition in self.effective_requirements(template):
            if set(references(condition.args)) & removed:
                issues.append(CheckResult(condition.id or condition.predicate, 'fail', reason='goal_subject_missing_after'))
        return issues


def validate_pose(pose, path='pose'):
    keys(pose, {'frame', 'position', 'quaternion_wxyz'}, {'frame', 'position', 'quaternion_wxyz'}, path)
    require(pose['frame'] == 'world', path + '.frame', 'concrete pose must be world-frame')
    vector(pose['position'], path + '.position')
    vector(pose['quaternion_wxyz'], path + '.quaternion_wxyz', length=4)
    require(abs(sum(x*x for x in pose['quaternion_wxyz']) - 1) <= 2e-6,
            path + '.quaternion_wxyz', 'normalized wxyz quaternion required')


@dataclass(frozen=True)
class ExecutableDSL(JsonContract):
    proposal_id: str
    sample_id: str
    operations: list
    sampled_parameters: dict = field(default_factory=dict)
    dsl_version: str = VERSION

    def __post_init__(self):
        require(self.dsl_version == VERSION, 'dsl_version', 'unsupported version')
        text(self.proposal_id, 'proposal_id')
        text(self.sample_id, 'sample_id')
        require(isinstance(self.operations, list) and bool(self.operations), 'operations', 'nonempty array required')
        for i, operation in enumerate(self.operations):
            path = f'operations[{i}]'
            keys(operation, {'op', 'instance', 'pose', 'asset_id', 'region_id'}, {'op', 'instance'}, path)
            require(operation['op'] in OPERATIONS, path + '.op', 'unsupported operation')
            text(operation['instance'], path + '.instance')
            if operation['op'] == 'remove':
                require(set(operation) == {'op', 'instance'}, path, 'remove has no pose or asset')
            else:
                validate_pose(operation.get('pose'), path + '.pose')
                if 'region_id' in operation:
                    text(operation['region_id'], path + '.region_id')
                if operation['op'] == 'add':
                    text(operation.get('asset_id'), path + '.asset_id')
                else:
                    require('asset_id' not in operation, path + '.asset_id', 'asset only belongs to add')
        from .case_schema import json_value
        require(isinstance(self.sampled_parameters, dict), 'sampled_parameters', 'object required')
        json_value(self.sampled_parameters, 'sampled_parameters')

    def to_runtime_operations(self):
        """Format adapter only; callers still need the S3 generic construction entry."""
        result = copy.deepcopy(self.operations)
        for operation in result:
            if operation['op'] == 'remove':
                operation['op'] = 'delete'
            else:
                pose = operation['pose']
                operation['pose'] = pose['position'] + pose['quaternion_wxyz']
        return result


def relative_quaternion(before, axis, angle, frame='world'):
    """Apply relative radians about the current world/local axis, with no translation."""
    validate_pose({'frame': 'world', 'position': [0, 0, 0], 'quaternion_wxyz': before})
    vector(axis, 'axis', nonzero=True)
    number(angle, 'angle')
    require(frame in ('world', 'object'), 'frame', 'world or object required')
    norm = math.sqrt(sum(x*x for x in axis))
    delta = [math.cos(angle/2)] + [x/norm*math.sin(angle/2) for x in axis]
    a, b = (delta, before) if frame == 'world' else (before, delta)
    w, x, y, z = a
    W, X, Y, Z = b
    result = [w*W-x*X-y*Y-z*Z, w*X+x*W+y*Z-z*Y,
              w*Y-x*Z+y*W+z*X, w*Z+x*Y-y*X+z*W]
    norm = math.sqrt(sum(v*v for v in result))
    return [v/norm for v in result]
