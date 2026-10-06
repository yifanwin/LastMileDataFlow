"""S0 JSON contracts. These describe cases; they do not execute or approve edits."""
import copy
from dataclasses import asdict, dataclass, field, fields
import json
import math
import re


VERSION = '0.1'
PREDICATES = {
    'supported': ('boolean', 1), 'supported_by': ('boolean', 2),
    'distance_xy': ('number', 2), 'distance_3d': ('number', 2),
    'direction_angle': ('number', 2), 'inside_region': ('boolean', 2),
}


class ContractError(ValueError):
    def __init__(self, path, message):
        self.path = path
        self.message = message
        super().__init__(f'{path}: {message}')


def require(ok, path, message):
    if not ok:
        raise ContractError(path, message)


def number(value, path, *, positive=False):
    require(type(value) in (int, float) and math.isfinite(value), path, 'finite number required')
    require(not positive or value > 0, path, 'positive number required')


def text(value, path):
    require(isinstance(value, str) and bool(value.strip()), path, 'nonempty string required')


def identifier(value, path):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value) is not None,
            path, 'symbol name required (without $)')


def vector(value, path, *, length=3, nonzero=False):
    require(isinstance(value, list) and len(value) == length, path, f'{length}-element array required')
    for i, item in enumerate(value):
        number(item, f'{path}[{i}]')
    require(not nonzero or sum(x*x for x in value) > 1e-12, path, 'nonzero vector required')


def bounds(value, path):
    vector(value, path, length=2)
    require(value[0] <= value[1], path, 'lower bound exceeds upper bound')


def json_value(value, path):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractError(path, 'finite JSON value required') from exc


def strings(value, path):
    require(isinstance(value, list), path, 'array required')
    for i, item in enumerate(value):
        text(item, f'{path}[{i}]')


def references(value):
    """Return role/part references, never interpret a predicate expression string."""
    if isinstance(value, str) and value.startswith('$'):
        require(re.fullmatch(r'\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?', value) is not None,
                'reference', 'malformed symbolic reference')
        yield value[1:].split('.', 1)[0]
    elif isinstance(value, dict):
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


class JsonContract:
    @classmethod
    def from_dict(cls, value):
        require(isinstance(value, dict), cls.__name__, 'object required')
        require(all(isinstance(k, str) for k in value), cls.__name__, 'string keys required')
        allowed = {f.name for f in fields(cls) if f.init}
        extra = set(value) - allowed
        require(not extra, cls.__name__, f'unknown fields: {sorted(extra)}')
        try:
            return cls(**copy.deepcopy(value))
        except TypeError as exc:
            raise ContractError(cls.__name__, str(exc)) from exc

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_json(cls, raw):
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ContractError(cls.__name__, 'invalid JSON') from exc
        return cls.from_dict(value)


def nested(cls, value, path):
    try:
        return copy.deepcopy(value) if isinstance(value, cls) else cls.from_dict(value)
    except ContractError as exc:
        raise ContractError(f'{path}.{exc.path}', exc.message) from exc


def nested_list(cls, values, path):
    require(isinstance(values, list), path, 'array required')
    return [nested(cls, v, f'{path}[{i}]') for i, v in enumerate(values)]


@dataclass(frozen=True)
class RoleSpec(JsonContract):
    type: str
    required: bool = True

    def __post_init__(self):
        text(self.type, 'role.type')
        require(type(self.required) is bool, 'role.required', 'boolean required')


@dataclass(frozen=True)
class ParameterSpec(JsonContract):
    source: str
    range: list | None = None
    value: object = None

    def __post_init__(self):
        require(self.source in ('user_input', 'difficulty_profile', 'heuristic_default'),
                'parameter.source', 'explicit parameter source required')
        require((self.range is None) != (self.value is None), 'parameter', 'provide range or value, not both')
        if self.range is not None:
            bounds(self.range, 'parameter.range')
        else:
            json_value(self.value, 'parameter.value')


@dataclass(frozen=True)
class Condition(JsonContract):
    predicate: str
    args: list
    id: str | None = None
    value: bool | None = None
    range: list | dict | None = None
    min: float | None = None
    max: float | None = None
    required: bool = True
    tolerance: float = 1e-6

    def __post_init__(self):
        require(isinstance(self.predicate, str) and self.predicate in PREDICATES,
                'condition.predicate', 'unsupported predicate vocabulary')
        kind, arity = PREDICATES[self.predicate]
        require(isinstance(self.args, list) and len(self.args) == arity, 'condition.args', f'{arity} arguments required')
        for i, arg in enumerate(self.args):
            path = f'condition.args[{i}]'
            if isinstance(arg, str):
                text(arg, path)
            elif self.predicate == 'direction_angle' and isinstance(arg, dict):
                if set(arg) == {'vector', 'frame'} and arg['frame'] == 'world':
                    vector(arg['vector'], path, nonzero=True)
                elif set(arg) == {'node', 'axis_local'}:
                    text(arg['node'], path + '.node')
                    vector(arg['axis_local'], path, nonzero=True)
                elif set(arg) == {'parameter'}:
                    identifier(arg['parameter'], path)
                else:
                    raise ContractError(path, 'explicit world vector, local node axis or parameter required')
            else:
                raise ContractError(path, 'node reference required')
        require(type(self.required) is bool, 'condition.required', 'boolean required')
        if self.id is not None:
            text(self.id, 'condition.id')
        number(self.tolerance, 'condition.tolerance')
        require(0 <= self.tolerance <= .001, 'condition.tolerance', 'tolerance must be between 0 and .001 SI units')
        if kind == 'boolean':
            require(type(self.value) is bool and self.range is None and self.min is None and self.max is None,
                    'condition', 'boolean predicate requires only value')
        else:
            require(self.value is None, 'condition.value', 'numeric predicate cannot use value')
            require((self.range is not None) != (self.min is not None or self.max is not None),
                    'condition', 'provide range or min/max')
            if isinstance(self.range, dict):
                require(set(self.range) == {'parameter'}, 'condition.range', 'parameter reference required')
                identifier(self.range['parameter'], 'condition.range.parameter')
            elif self.range is not None:
                bounds(self.range, 'condition.range')
            for name in ('min', 'max'):
                if getattr(self, name) is not None:
                    number(getattr(self, name), 'condition.' + name)
            require(self.min is None or self.max is None or self.min <= self.max,
                    'condition', 'min exceeds max')


@dataclass(frozen=True)
class CaseTemplate(JsonContract):
    case_type: str
    intent: str
    roles: dict
    parameters: dict = field(default_factory=dict)
    requirements: list = field(default_factory=list)
    invariants: list = field(default_factory=list)
    semantic_checks: list = field(default_factory=list)
    pending_hypotheses: list = field(default_factory=list)
    template_version: str = VERSION
    source_description: str | None = None

    def __post_init__(self):
        require(self.template_version == VERSION, 'template_version', 'unsupported version')
        text(self.case_type, 'case_type')
        text(self.intent, 'intent')
        if self.source_description is not None:
            text(self.source_description, 'source_description')
        require(isinstance(self.roles, dict) and bool(self.roles), 'roles', 'nonempty role map required')
        require(isinstance(self.parameters, dict), 'parameters', 'parameter map required')
        for name in (*self.roles, *self.parameters):
            identifier(name, 'symbol')
        require(not set(self.roles) & set(self.parameters), 'symbols', 'role and parameter names overlap')
        object.__setattr__(self, 'roles', {k: nested(RoleSpec, v, 'roles.' + k) for k, v in self.roles.items()})
        object.__setattr__(self, 'parameters', {k: nested(ParameterSpec, v, 'parameters.' + k) for k, v in self.parameters.items()})
        for name in ('requirements', 'invariants'):
            object.__setattr__(self, name, nested_list(Condition, getattr(self, name), name))
        for name in ('semantic_checks', 'pending_hypotheses'):
            strings(getattr(self, name), name)
        ids = [c.id for c in self.requirements + self.invariants if c.id is not None]
        require(len(ids) == len(set(ids)), 'conditions.id', 'duplicate condition ID')
        for c in self.requirements + self.invariants:
            for arg in c.args:
                if isinstance(arg, str):
                    require(arg.startswith('$'), 'template.args', 'templates cannot contain concrete instance IDs')
                if isinstance(arg, dict) and 'node' in arg:
                    require(arg['node'].startswith('$'), 'template.args.node', 'symbolic node required')
            require(set(references(c.args)) <= set(self.roles), 'template.args', 'undeclared role reference')
            parameter_refs = []
            if isinstance(c.range, dict):
                parameter_refs.append(c.range['parameter'])
            parameter_refs.extend(a['parameter'] for a in c.args if isinstance(a, dict) and 'parameter' in a)
            for name in parameter_refs:
                require(name in self.parameters, 'template.parameters', f'unknown parameter {name}')
            if isinstance(c.range, dict):
                require(self.parameters[c.range['parameter']].range is not None,
                        'template.range', 'range parameter required')
            for arg in c.args:
                if isinstance(arg, dict) and 'parameter' in arg:
                    vector(self.parameters[arg['parameter']].value, 'template.direction_parameter', nonzero=True)


@dataclass(frozen=True)
class GenerationConfig(JsonContract):
    target_scene_count: int = 10
    proposals_per_round: int = 4
    samples_per_proposal: int = 16
    max_rounds: int = 5
    max_samples: int = 200
    max_agent_calls: int = 40
    timeout_s: float = 600
    seed: int = 42

    def __post_init__(self):
        for name in ('target_scene_count', 'proposals_per_round', 'samples_per_proposal',
                     'max_rounds', 'max_samples', 'max_agent_calls', 'seed'):
            value = getattr(self, name)
            minimum = 1 if name in ('target_scene_count', 'proposals_per_round', 'samples_per_proposal') else 0
            require(type(value) is int and value >= minimum, 'generation.' + name, 'invalid integer')
        number(self.timeout_s, 'generation.timeout_s', positive=True)


@dataclass(frozen=True)
class CaseEditRequest(JsonContract):
    case_description: str
    scene_source: dict
    task_context: dict = field(default_factory=dict)
    difficulty_profile: str | dict | None = None
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    request_version: str = VERSION

    def __post_init__(self):
        require(self.request_version == VERSION, 'request_version', 'unsupported version')
        text(self.case_description, 'case_description')
        require(isinstance(self.scene_source, dict), 'scene_source', 'object required')
        require({'scene_id', 'xml_path'} <= set(self.scene_source) <=
                {'scene_id', 'xml_path', 'metadata_path', 'dataset', 'split'}, 'scene_source', 'invalid scene descriptor')
        for name, value in self.scene_source.items():
            if value is not None or name != 'metadata_path':
                text(value, 'scene_source.' + name)
        require(isinstance(self.task_context, dict), 'task_context', 'object required')
        json_value(self.task_context, 'task_context')
        require(self.difficulty_profile is None or isinstance(self.difficulty_profile, (str, dict)),
                'difficulty_profile', 'name or object required')
        json_value(self.difficulty_profile, 'difficulty_profile')
        object.__setattr__(self, 'generation', nested(GenerationConfig, self.generation, 'generation'))


@dataclass(frozen=True)
class CheckResult(JsonContract):
    item: str
    status: str
    required: bool = True
    value: object = None
    reason: str = ''
    evidence: dict = field(default_factory=dict)
    views: list = field(default_factory=list)

    def __post_init__(self):
        text(self.item, 'check.item')
        require(self.status in ('pass', 'fail', 'unknown'), 'check.status', 'invalid status')
        require(type(self.required) is bool, 'check.required', 'boolean required')
        require(isinstance(self.reason, str) and isinstance(self.evidence, dict), 'check', 'reason/evidence types invalid')
        json_value(self.value, 'check.value')
        json_value(self.evidence, 'check.evidence')
        strings(self.views, 'check.views')


def required_checks_pass(checks):
    parsed = nested_list(CheckResult, checks, 'checks')
    return all(c.status == 'pass' for c in parsed if c.required)


@dataclass(frozen=True)
class Review(JsonContract):
    sample_id: str
    verdict: str
    checks: list
    unverified_claims: list = field(default_factory=list)
    reason: str = ''
    review_version: str = VERSION

    def __post_init__(self):
        text(self.sample_id, 'sample_id')
        require(self.review_version == VERSION, 'review_version', 'unsupported version')
        require(self.verdict in ('pass', 'fail', 'uncertain'), 'verdict', 'invalid review verdict')
        object.__setattr__(self, 'checks', nested_list(CheckResult, self.checks, 'checks'))
        require(bool(self.checks), 'checks', 'at least one check required')
        require(self.verdict != 'pass' or any(c.required for c in self.checks),
                'checks', 'pass requires a required check')
        require(self.verdict != 'pass' or required_checks_pass(self.checks), 'verdict', 'required checks did not pass')
        strings(self.unverified_claims, 'unverified_claims')
        require(isinstance(self.reason, str), 'reason', 'string required')


@dataclass(frozen=True)
class RunResult(JsonContract):
    run_id: str
    status: str
    target_scene_count: int
    attempted_count: int = 0
    accepted_count: int = 0
    accepted_samples: list = field(default_factory=list)
    results: dict = field(default_factory=lambda: {
        'scene_validity': 'unknown', 'edit_satisfaction': 'unknown',
        'semantic_alignment': 'unknown', 'case_condition': 'unknown', 'task_completion': 'unknown'})
    reason: str = ''
    result_version: str = VERSION

    def __post_init__(self):
        require(self.result_version == VERSION, 'result_version', 'unsupported version')
        text(self.run_id, 'run_id')
        require(self.status in ('completed', 'partial', 'budget_exhausted', 'failed', 'interrupted', 'infrastructure_error'),
                'status', 'invalid run status')
        for name in ('target_scene_count', 'attempted_count', 'accepted_count'):
            v = getattr(self, name)
            require(type(v) is int and v >= (1 if name == 'target_scene_count' else 0), name, 'invalid count')
        strings(self.accepted_samples, 'accepted_samples')
        require(len(set(self.accepted_samples)) == len(self.accepted_samples) == self.accepted_count <= self.attempted_count,
                'accepted_count', 'sample identities/counts disagree')
        require(self.status != 'completed' or self.accepted_count >= self.target_scene_count, 'status', 'target count not reached')
        require(isinstance(self.results, dict) and set(self.results) ==
                {'scene_validity', 'edit_satisfaction', 'semantic_alignment', 'case_condition', 'task_completion'},
                'results', 'separate result fields required')
        require(self.results['scene_validity'] in ('valid', 'invalid', 'unknown'), 'results.scene_validity', 'invalid status')
        for name in ('edit_satisfaction', 'semantic_alignment'):
            require(self.results[name] in ('pass', 'fail', 'unknown'), 'results.' + name, 'invalid status')
        require(self.results['case_condition'] == self.results['task_completion'] == 'unknown',
                'results', 'v0.1 construction cannot claim robot case/task verification')
        require(not self.accepted_count or (self.results['scene_validity'] == 'valid' and
                self.results['edit_satisfaction'] == self.results['semantic_alignment'] == 'pass'),
                'accepted_count', 'accepted subset requires rule/visual pass')
        require(isinstance(self.reason, str), 'reason', 'string required')
