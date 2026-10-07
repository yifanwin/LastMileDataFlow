"""Choose local context and preliminary construction direction."""
from dataclasses import dataclass, field
from ..construction.case_schema import JsonContract, number, require, strings, text
from ..scenes.local_context import summarize
from .prompts import STRATEGIST

@dataclass(frozen=True)
class ConstructionPlan(JsonContract):
    decision: str
    context_id: str | None
    radius_m: float
    edit_directions: list
    expected_layout: str
    contrast_spec: list
    asset_requests: list
    rationale: str
    unverified_claims: list = field(default_factory=list)

    def __post_init__(self):
        require(self.decision in ('select', 'expand', 'no_context'), 'decision', 'unknown strategy decision')
        number(self.radius_m, 'radius_m', positive=True)
        for name in ('expected_layout', 'rationale'):
            text(getattr(self, name), name)
        for name in ('edit_directions', 'unverified_claims'):
            strings(getattr(self, name), name)
        require(set(self.edit_directions) <= {'move', 'rotate', 'add', 'remove'}, 'edit_directions', 'unknown operation')
        require(self.decision != 'select' or bool(self.edit_directions), 'edit_directions', 'select requires an edit direction')
        require(isinstance(self.contrast_spec, list) and isinstance(self.asset_requests, list), 'plan', 'arrays required')
        for c in self.contrast_spec:
            require(isinstance(c, dict) and set(c) == {'work_region_id', 'role', 'expected_mechanism'}, 'contrast_spec', 'invalid contrast')
            require(c['role'] in ('base_space_constrained', 'arm_unfavorable', 'path_obstructed', 'preferred'), 'contrast_spec.role', 'unknown mechanism role')
            for value in c.values():
                text(value, 'contrast_spec')
        for a in self.asset_requests:
            require(isinstance(a, dict) and set(a) == {'categories', 'intended_role', 'size_constraints_m'}, 'asset_requests', 'invalid asset request')
            strings(a['categories'], 'categories')
            text(a['intended_role'], 'intended_role')
            require(isinstance(a['size_constraints_m'], dict), 'size_constraints_m', 'object required')
            from ..construction.case_schema import bounds
            for key, value in a['size_constraints_m'].items():
                require(key in ('width', 'depth', 'height'), 'size_constraints_m', 'unknown dimension')
                bounds(value, key)
                require(value[0] > 0, key, 'positive dimension required')

def choose_strategy(template, contexts, gateway, config, *, categories=(), feedback=()):
    def parse(value):
        plan = ConstructionPlan.from_dict(value)
        require(config.radius_m <= plan.radius_m <= config.max_radius_m, 'radius_m', 'outside expansion budget')
        if plan.decision != 'no_context':
            require(plan.context_id in {c.context_id for c in contexts}, 'context_id', 'not an offered context')
            context = next(c for c in contexts if c.context_id == plan.context_id)
            require(all(c['work_region_id'] in {r['region_id'] for r in context.work_regions()} for c in plan.contrast_spec),
                    'contrast_spec', 'unknown work region')
            if template.case_type == 'case1.5' and plan.decision == 'select':
                required_roles = {'base_space_constrained', 'arm_unfavorable', 'preferred'}
                contrasts = [c for c in plan.contrast_spec if c['role'] in required_roles]
                require(len(contrasts) == 3 and required_roles == {c['role'] for c in contrasts},
                        'contrast_spec', 'case1.5 requires exactly one hypothesis for each of A/B/C')
                require(len({c['work_region_id'] for c in contrasts}) == 3,
                        'contrast_spec', 'A/B/C must refer to different work regions')
        require(all(set(a['categories']) <= set(categories) for a in plan.asset_requests), 'categories', 'absent from library')
        return plan
    return gateway.call('strategist', STRATEGIST, {'template': template.to_dict(),
        'contexts': [summarize(c) for c in contexts], 'available_asset_categories': list(categories),
        'radius_budget_m': [config.radius_m, config.max_radius_m], 'feedback': list(feedback)}, parse)
