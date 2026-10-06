"""Agent1: one fresh normalization per run, no retrieval or template cache."""
from .case_gateway import COMMON, CONDITIONS
from ..construction.case_schema import CaseTemplate


SYSTEM = COMMON + CONDITIONS + '''
Normalize the raw description to CaseTemplate v0.1 with fields:
case_type, intent, roles {role:{type,required:true}}, parameters
{name:{source:"user_input/difficulty_profile/heuristic_default",range:[lo,hi] OR value}},
requirements, invariants, semantic_checks [text], pending_hypotheses [text].
Use no concrete object IDs, exact scene coordinates or poses. Abstract required roles
and supported final layouts are okay. Invariants apply ONLY at settled before and
settled after endpoints. Put geometric proxies into requirements, unverified robot
difficulty into pending_hypotheses. Preserve every explicit user requirement and
original intent. Distances/angles not specified by the user must identify their
difficulty_profile source or be clearly labelled heuristic_default, not user_input.
For numeric requirements/invariants ALWAYS use range:{"parameter":"name"} and
declare that named range in parameters, so its provenance is retained. A source of
difficulty_profile is allowed only when the request supplies an explicit profile.
Do not infer specific reach limits. IMPORTANT: a relative request such as "farther
from the initial station" does NOT specify an absolute final distance interval.
With no explicit numeric user range/profile, express the before/after increase in
semantic_checks; do NOT invent a hard absolute-distance range that could exclude
all actually farther layouts. Absolute numeric requirements should reflect explicit
user quantities/profile, or an intentionally requested geometric proxy with declared
heuristic provenance. No absolute distance condition is required merely to use a
robot_station role. Both endpoints still require actual supported invariants. Avoid impossible optional-as-required directions.
semantic_checks must include a meaningful visible criterion for the requested phenomenon.
Only normalize USER-requested scene phenomena. semantic_checks must be decidable for
ONE settled before/after image pair. Never add candidate-set coverage, sampling,
deduplication, budgets, or diversity-of-the-entire-run as required scene checks;
the PROGRAM owns those, and one image pair cannot establish them. Do not add a
robot_station role unless the description actually needs robot-relative context.
Use implemented role types: object/manipulable_object/movable_object,
support_surface/surface, robot_station/station, region, reference_object, obstacle_object.
Roles bind actual scene objects/parts/regions or station_start, NOT imaginary reference
locations. Do NOT introduce initial_target_location or initial_orientation roles:
these are not graph nodes. Changes from BEFORE are assessed by paired semantic checks
and non-baseline layout deduplication, not distance between two versions of one node.
One invariant is automatically checked at both endpoints; don't duplicate it by stage.
Use only the listed predicate vocabulary; do not encode function expressions as strings.'''


def normalize_case(request, gateway):
    def parse(value):
        template = CaseTemplate.from_dict(value)
        normalized = template.to_dict()
        normalized['source_description'] = request.case_description
        if not template.semantic_checks:
            raise ValueError('at least one semantic check required')
        process_terms = ('候选集合', '候选布局', '采样', '去重', 'candidate set', 'sampling', 'deduplicat')
        if any(term in check.lower() for check in template.semantic_checks for term in process_terms):
            raise ValueError('semantic_checks must concern ONE scene pair, not candidate-set processing')
        role_types = {'object', 'manipulable_object', 'movable_object', 'support_surface', 'surface',
                      'robot_station', 'station', 'region', 'reference_object', 'obstacle_object'}
        if any(role.type not in role_types for role in template.roles.values()):
            raise ValueError('unimplemented role type; before/after changes belong in semantic_checks')
        for condition in template.requirements + template.invariants:
            if condition.predicate in ('distance_xy', 'distance_3d', 'direction_angle') and not isinstance(condition.range, dict):
                raise ValueError('normalized numeric conditions require named parameter provenance')
        if request.difficulty_profile is None and any(p.source == 'difficulty_profile' for p in template.parameters.values()):
            raise ValueError('difficulty_profile source requires an explicit input profile')
        return CaseTemplate.from_dict(normalized)
    return gateway.call('normalizer', SYSTEM, {'case_description': request.case_description,
                        'difficulty_profile': request.difficulty_profile, 'task_context': request.task_context}, parse)
