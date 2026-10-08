"""Human-authored operational case definitions; never execute Agent predicates."""
import copy
from dataclasses import dataclass
from pathlib import Path
import math

from ..io import read_json, digest

PREDICATES = {'standable', 'strip_clearance', 'reach_ok', 'plan_ok', 'corridor',
              'handle_only', 'counterfactual', 'yaw_exclusion', 'ground_path', 'start_edge_unreachable'}
CASE_TYPES = {'case1', 'case1.5', 'case2', 'case3', 'case1-S'}


@dataclass(frozen=True)
class CaseSpec:
    schema_version: str
    case_type: str
    description: str
    preconditions: dict
    predicates: list
    move_types: list
    attribution: str
    counterfactual: str | None
    edit_recipes: list
    thresholds: dict

    @classmethod
    def from_dict(cls, value):
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('CaseSpec: missing or unknown fields')
        return cls(**copy.deepcopy(value))

    def __post_init__(self):
        if self.schema_version != 'case-spec-v1' or self.case_type not in CASE_TYPES:
            raise ValueError('unsupported CaseSpec version/type')
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError('description required')
        if not isinstance(self.preconditions, dict) or set(self.preconditions) != {
                'min_edges', 'target_categories', 'requires_handle_annotation', 'min_surface_objects'}:
            raise ValueError('invalid preconditions')
        p = self.preconditions
        if type(p['min_edges']) is not int or not 1 <= p['min_edges'] <= 4 or type(p['min_surface_objects']) is not int or p['min_surface_objects'] < 0:
            raise ValueError('invalid prerequisite counts')
        if type(p['requires_handle_annotation']) is not bool:
            raise ValueError('invalid annotation flag')
        if not isinstance(p['target_categories'], list) or not p['target_categories'] or any(not isinstance(x, str) or not x for x in p['target_categories']):
            raise ValueError('target category whitelist required')
        for name in ('predicates', 'move_types', 'edit_recipes'):
            values = getattr(self, name)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
                raise ValueError('invalid ' + name)
        if not self.predicates or set(self.predicates)-PREDICATES:
            raise ValueError('unknown predicate')
        expected_moves = {'case1': ['switch_edge'], 'case1.5': ['switch_edge'],
                          'case2': ['same_edge', 'switch_edge'], 'case3': ['same_edge', 'switch_edge'],
                          'case1-S': ['same_edge']}
        expected_attr = {'case1': 'reach', 'case1.5': 'standing', 'case2': 'handle', 'case3': 'corridor', 'case1-S': 'reach'}
        expected_counter = {'case1': None, 'case1.5': None, 'case1-S': None,
                            'case2': 'body_grasp', 'case3': 'remove_corridor_clutter'}
        if self.move_types != expected_moves[self.case_type] or self.attribution != expected_attr[self.case_type] or self.counterfactual != expected_counter[self.case_type]:
            raise ValueError('case semantics cannot be weakened by configuration')
        if self.case_type == 'case2' and not p['requires_handle_annotation']:
            raise ValueError('case2 requires human-checked handle annotation')
        if not isinstance(self.thresholds, dict) or set(self.thresholds) != {'edge_gap_max_m', 'min_success_stations', 'translation_min_m'}:
            raise ValueError('invalid thresholds')
        for key, value in self.thresholds.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid threshold: ' + key)
        if type(self.thresholds['min_success_stations']) is not int or self.thresholds['min_success_stations'] < 2:
            raise ValueError('success region must not be a single extreme sample')

    def to_dict(self):
        return copy.deepcopy(self.__dict__)

    @property
    def spec_digest(self):
        return digest(self.to_dict())


def load_case_spec(path):
    return CaseSpec.from_dict(read_json(Path(path)))
