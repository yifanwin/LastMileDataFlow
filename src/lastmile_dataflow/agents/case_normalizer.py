"""Agent1: one fresh normalization per run, no retrieval or template cache."""
import re
from .case_gateway import CONDITIONS
from .prompts import NORMALIZER
from ..construction.scene_request import MobileCaseTemplate
from ..construction.case_schema import PREDICATES, require
from ..construction.case_schema import CaseTemplate


SYSTEM = NORMALIZER


def _normalize_rule_template(request, gateway):
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


def normalize_case(request, gateway):
    if not hasattr(request, "case_type"):
        return _normalize_rule_template(request, gateway)
    def parse(value):
        template = MobileCaseTemplate.from_dict(value)
        require(template.case_type == request.case_type and template.task_type == request.task_type
                and template.objective_mode == request.objective_mode, 'template', 'input objective/type changed')
        require('target' in template.roles, 'roles.target', 'target required')
        forbidden = ('底盘移动后', '移动底盘后', '实际底盘运动', '机器人移动后', '抓取成功率', '抓取成功',
                     '机械臂可达', 'task success', 'robot reachability')
        def task_claim(check):
            for clause in re.split(r'[。；;\n，,]|但是|但|然而', check.lower()):
                if not any(word in clause for word in forbidden):
                    continue
                # A limitation is not a demand to execute a task. The previous
                # keyword-only guard rejected "不将…作为抓取成功的证明" itself.
                disclaimer = (re.search(r'不(?:把|将|以).*(?:证明|证据|依据)', clause)
                              or re.search(r'不能(?:证明|确认|判断|验证)', clause))
                if not disclaimer:
                    return True
            return False
        require(not any(task_claim(check) for check in template.semantic_checks),
                'semantic_checks', 'actual motion/reachability/success belongs in task_hypotheses, not static visual checks')
        require(template.roles['target'].required, 'roles.target', 'observed target must remain required')
        require(request.difficulty_profile is not None or not any(p.source == 'difficulty_profile' for p in template.parameters.values()),
                'parameters', 'difficulty_profile source unavailable')
        for c in template.requirements + template.invariants:
            if PREDICATES[c.predicate][0] == 'number':
                require(isinstance(c.range, dict), 'condition', 'numeric condition needs named parameter provenance')
        return MobileCaseTemplate.from_dict({**template.to_dict(), 'source_description': request.case_description})
    return gateway.call('normalizer', NORMALIZER, {'case_description': request.case_description,
        'case_type': request.case_type, 'task_type': request.task_type, 'objective_mode': request.objective_mode,
        'difficulty_profile': request.difficulty_profile, 'registered_predicates': CONDITIONS,
        'condition_contract': {'boolean': {'predicate': 'supported', 'args': ['$target'], 'value': True},
                               'numeric': {'predicate': 'distance_xy', 'args': ['$target', '$robot_start'],
                                           'range': {'parameter': 'named_range'}}},
        'output_shape_example_not_case_requirements': {
            'case_type': request.case_type, 'intent': '保留用户机制的抽象描述',
            'task_type': request.task_type, 'objective_mode': request.objective_mode,
            'roles': {'target': {'type': 'manipulable_object', 'required': True},
                      'support': {'type': 'support_surface', 'required': True}},
            'parameters': {}, 'requirements': [],
            'invariants': [{'predicate': 'supported_by', 'args': ['$target', '$support'], 'value': True}],
            'semantic_checks': ['只填写前后可见的布局变化'], 'pending_hypotheses': [],
            'task_hypotheses': ['保留用户要求、待真实仿真验证的任务假设'], 'assumptions': [],
            'template_version': '0.1'}}, parse)
