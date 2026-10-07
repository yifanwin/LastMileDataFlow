"""One wire contract for prompts, HTTP structured outputs and local validation.

Domain parsers additionally check geometry, provenance and cross-field references.
Legacy rule-only calls deliberately retain their existing domain contracts.
"""
from dataclasses import dataclass, field
import copy

from jsonschema import Draft202012Validator

from ..construction.case_schema import JsonContract, ContractError, Review, PREDICATES, require, strings, text


def obj(properties, required=None, *, additional=False):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else list(required),
            'additionalProperties': additional}


def arr(item, **limits):
    return {'type': 'array', 'items': item, **limits}


S = {'type': 'string', 'minLength': 1}
N = {'type': 'number'}
B = {'type': 'boolean'}
RANGE = arr(N, minItems=2, maxItems=2)


def enum(values):
    return {'type': 'string', 'enum': list(values)} if values else {'not': {}}


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


def condition_schema():
    reference = {'anyOf': [S, obj({'vector': arr(N, minItems=3, maxItems=3), 'frame': enum(['world'])}),
                  obj({'node': S, 'axis_local': arr(N, minItems=3, maxItems=3)}), obj({'parameter': S})]}
    variants = []
    for predicate, (kind, arity) in PREDICATES.items():
        properties = {'predicate': enum([predicate]),
            'args': arr(reference if predicate == 'direction_angle' else S, minItems=arity, maxItems=arity),
            'id': nullable(S), 'value': {'type': 'null'},
            'range': nullable({'anyOf': [RANGE, obj({'parameter': S})]}), 'min': nullable(N), 'max': nullable(N),
            'required': B, 'tolerance': {'type': 'number', 'minimum': 0, 'maximum': .001}}
        required = ['predicate', 'args']
        if kind == 'boolean':
            properties.update(value=B, **{k: {'type': 'null'} for k in ('range', 'min', 'max')})
            required.append('value')
        variants.append(obj(properties, required=required))
    return {'anyOf': variants}


def dsl_schema(nodes):
    role = {'type': 'string', 'pattern': r'^\$[A-Za-z_][A-Za-z0-9_]*$'}
    rotation = obj({'axis': arr(N, minItems=3, maxItems=3), 'frame': enum(['world', 'object']),
                    'angle': {'anyOf': [obj({'value': N}), obj({'range': RANGE})]},
                    'pivot': enum(['object_origin'])}, required=['axis', 'angle'])
    xy = obj({'mode': enum(['candidate', 'uniform', 'grid', 'sample_inside']), 'margin': N,
              'steps': {'type': 'integer', 'minimum': 1}, 'x': RANGE, 'y': RANGE}, required=['mode'])
    search = obj({'support': S, 'region': {'anyOf': [S, obj({'relative_to': S, 'distance': RANGE})]},
                  'xy': xy, 'rotation': rotation, 'frame': enum(['world']),
                  'offset': obj({k: {'anyOf': [N, RANGE]} for k in ('x', 'y', 'z')}, required=[])}, required=[])
    operations = [obj({'op': enum(['move']), 'subject': role, 'search_space': search, 'carry_supported': B},
                      required=['op', 'subject', 'search_space']),
                  obj({'op': enum(['rotate']), 'subject': role, **rotation['properties']}, required=['op', 'subject', 'axis', 'angle']),
                  obj({'op': enum(['remove']), 'subject': role}),
                  obj({'op': enum(['add']), 'bind_as': role, 'asset_selector': obj({'asset_id': S, 'category': arr(S), 'type': S,
                       'size': obj({k: RANGE for k in ('width', 'depth', 'height')}, required=[])}, required=[]),
                       'search_space': search}, required=['op', 'bind_as', 'asset_selector', 'search_space'])]
    return obj({'proposal_id': S, 'bindings': obj({}, required=[], additional=enum(nodes)),
                'operations': arr({'anyOf': operations}, minItems=1), 'goals': arr(condition_schema()),
                'invariants': arr(condition_schema()), 'sampling': obj({'selection': enum(['coverage', 'random', 'grid']),
                'max_samples': {'type': 'integer', 'minimum': 1}}, required=[]), 'description': {'type': 'string'},
                'dsl_version': enum(['0.1'])}, required=['proposal_id', 'bindings', 'operations'])


@dataclass(frozen=True)
class InformationRequest(JsonContract):
    kind: str
    reason: str
    node_ids: list = field(default_factory=list)
    work_region_ids: list = field(default_factory=list)
    view_hint: str = 'auto'
    radius_m: float | None = None
    categories: list = field(default_factory=list)

    def __post_init__(self):
        require(self.kind in ('aux_view', 'expand_context', 'assets', 'change_target'), 'information_request.kind', 'unknown request')
        text(self.reason, 'information_request.reason')
        for name in ('node_ids', 'work_region_ids', 'categories'):
            strings(getattr(self, name), 'information_request.' + name)
        require(self.view_hint in ('auto', 'top', 'side', 'overview'), 'view_hint', 'unknown view hint')
        if self.kind == 'expand_context':
            from ..construction.case_schema import number
            number(self.radius_m, 'radius_m', positive=True)
        if self.kind == 'assets':
            require(bool(self.categories), 'categories', 'asset categories required')
        if self.kind == 'change_target':
            require(len(self.node_ids) == 1, 'node_ids', 'one new target required')


def information_schema():
    return obj({'kind': enum(['aux_view', 'expand_context', 'assets', 'change_target']), 'reason': S,
                'node_ids': arr(S), 'work_region_ids': arr(S), 'view_hint': enum(['auto', 'top', 'side', 'overview']),
                'radius_m': nullable(N), 'categories': arr(S)}, required=['kind', 'reason'])


def validate_information(value, context):
    request = InformationRequest.from_dict(value)
    require(set(request.node_ids) <= set(context.graph.nodes), 'node_ids', 'not in current context')
    require(set(request.work_region_ids) <= {r['region_id'] for r in context.work_regions()},
            'work_region_ids', 'unknown work region')
    return request


@dataclass(frozen=True)
class ConstructionReview(Review):
    information_request: dict | None = None
    agent_assessment: dict = field(default_factory=dict)


def output_schema(role, payload):
    if role == 'normalizer' and 'case_type' in payload:
        role_spec = obj({'type': enum(['object', 'manipulable_object', 'movable_object', 'support_surface', 'surface',
                       'robot_station', 'station', 'region', 'reference_object', 'obstacle_object']), 'required': B})
        parameter = {'anyOf': [obj({'source': enum(['user_input', 'difficulty_profile', 'heuristic_default']), 'range': RANGE}),
                     obj({'source': enum(['user_input', 'difficulty_profile', 'heuristic_default']),
                          'value': {'anyOf': [N, B, S, arr(N), obj({}, required=[], additional=True)]}})]}
        return obj({'case_type': enum([payload['case_type']]), 'intent': S, 'roles': obj({}, required=[], additional=role_spec),
                    'parameters': obj({}, required=[], additional=parameter), 'requirements': arr(condition_schema()),
                    'invariants': arr(condition_schema()), 'semantic_checks': arr(S, minItems=1), 'pending_hypotheses': arr(S),
                    'task_type': enum([payload['task_type']]), 'objective_mode': enum([payload['objective_mode']]),
                    'task_hypotheses': arr(S, minItems=1), 'assumptions': arr(S), 'template_version': enum(['0.1']),
                    'source_description': nullable(S)},
                   required=['case_type', 'intent', 'roles', 'semantic_checks', 'task_type', 'objective_mode', 'task_hypotheses', 'assumptions'])
    if role == 'strategist':
        return obj({'decision': enum(['select', 'expand', 'no_context']), 'context_id': nullable(enum([c['context_id'] for c in payload['contexts']])),
                    'radius_m': {'type': 'number', 'minimum': payload['radius_budget_m'][0], 'maximum': payload['radius_budget_m'][1]},
                    'edit_directions': arr(enum(['move', 'rotate', 'add', 'remove'])), 'expected_layout': S,
                    'contrast_spec': arr(obj({'work_region_id': enum([r['region_id'] for c in payload['contexts'] for r in c['work_regions']]),
                      'role': enum(['base_space_constrained', 'arm_unfavorable', 'path_obstructed', 'preferred']), 'expected_mechanism': S})),
                    'asset_requests': arr(obj({'categories': arr(enum(payload['available_asset_categories'])), 'intended_role': S,
                      'size_constraints_m': obj({k: RANGE for k in ('width', 'depth', 'height')}, required=[])})),
                    'rationale': S, 'unverified_claims': arr(S)}, required=['decision', 'context_id', 'radius_m', 'edit_directions',
                      'expected_layout', 'contrast_spec', 'asset_requests', 'rationale'])
    if role == 'proposer' and 'context' in payload:
        return obj({'decision': enum(['propose', 'request_information', 'no_proposal']),
                    'information_request': nullable(information_schema()),
                    'proposals': arr(dsl_schema(payload['context']['graph']['nodes']), maxItems=payload['max_proposals'])})
    if role == 'reviewer' and payload.get('review_scope') == 'visible_construction_only':
        check = obj({'status': enum(['pass', 'fail', 'unknown']), 'reason': S,
                     'views': arr(enum([i['view'] for i in payload['view_registry']]))})
        return obj({'checks': obj({key: copy.deepcopy(check) for key in payload['required_visual_checks']}),
                    'information_request': nullable(information_schema()),
                    'agent_assessment': obj({'conclusion': enum(['likely_beneficial', 'unlikely_beneficial', 'unknown']),
                       'reason': S, 'preferred_regions': arr(enum([r['region_id'] for r in payload['before_context']['work_regions']]))})})
    return None


def validate_output(value, schema):
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.absolute_path)))
    if errors:
        error = errors[0]
        pointer = '/' + '/'.join(str(p) for p in error.absolute_path)
        raise ContractError(pointer, error.message[:1000])
