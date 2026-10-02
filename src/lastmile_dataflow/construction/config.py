"""Versioned build contract; v1 TaskConfig remains unchanged."""
from dataclasses import dataclass, field
import math
from pathlib import Path

from ..config import construct
from ..io import read_json


def number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'invalid {name}')


@dataclass(frozen=True)
class BuildProtocol:
    version: str = 'placement-v2'
    settle_s: float = 1.0
    max_settle_s: float = 2.0
    window_s: float = .25
    speed_m_s: float = .025
    angular_speed_rad_s: float = .10
    drift_m: float = .006
    rotation_rad: float = .04
    edge_margin_m: float = .01
    support_tolerance_m: float = .008
    penetration_m: float = .01
    protected_translation_m: float = .02
    protected_rotation_rad: float = .10
    robot_translation_m: float = .02
    robot_rotation_rad: float = .05
    flight_m: float = .25

    def __post_init__(self):
        if self.version != 'placement-v2':
            raise ValueError('unsupported build protocol')
        for name, value in self.__dict__.items():
            if name != 'version':
                number(value, name, True)
        if not self.window_s <= self.settle_s <= self.max_settle_s <= 10:
            raise ValueError('invalid settle window/budget')


@dataclass(frozen=True)
class BuildBudget:
    candidates: int = 48
    edits: int = 12
    repairs: int = 3
    agent_calls: int = 0
    timeout_s: float = 120

    def __post_init__(self):
        for name in ('candidates', 'edits', 'repairs', 'agent_calls'):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f'invalid budget {name}')
        number(self.timeout_s, 'timeout_s', True)


@dataclass(frozen=True)
class BuildConfig:
    schema_version: str
    task_id: str
    case_type: str
    target: str
    support: str
    operation: str = 'pick'
    region_geom: str | None = None
    robot_base: list | None = None
    editable: list = field(default_factory=list)
    allowed_operations: list = field(default_factory=lambda: ['move', 'rotate'])
    protected: list = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    initial_operations: list = field(default_factory=list)
    asset_pool: str | None = None
    protocol: BuildProtocol = field(default_factory=BuildProtocol)
    budget: BuildBudget = field(default_factory=BuildBudget)

    def __post_init__(self):
        if not isinstance(self.protocol,BuildProtocol) or not isinstance(self.budget,BuildBudget): raise ValueError('invalid nested build contract')
        if self.region_geom is not None and (not isinstance(self.region_geom,str) or not self.region_geom): raise ValueError('invalid region geom')
        if self.asset_pool is not None and not isinstance(self.asset_pool,str): raise ValueError('invalid asset pool path')
        if self.schema_version != '2.0' or self.case_type not in ('case1', 'case1.5', 'case2', 'case3'):
            raise ValueError('unsupported build schema or case')
        for name in ('task_id', 'target', 'support', 'operation'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f'invalid {name}')
        for name in ('editable', 'protected', 'allowed_operations'):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value) or len(value) != len(set(value)):
                raise ValueError(f'invalid {name}')
        if set(self.editable) & set(self.protected):
            raise ValueError('editable and protected overlap')
        if set(self.allowed_operations) - {'move', 'rotate', 'add', 'delete'}:
            raise ValueError('unsupported edit (joint initialization/scale not implemented)')
        if any(x.startswith('robot_0/') for x in self.editable):
            raise ValueError('robot is not editable')
        if self.robot_base is not None:
            if not isinstance(self.robot_base, list) or len(self.robot_base) != 3:
                raise ValueError('robot_base must be xyz yaw tuple: x,y,yaw')
            for x in self.robot_base:
                number(x, 'robot_base')
        if not isinstance(self.parameters, dict) or not isinstance(self.initial_operations, list):
            raise ValueError('invalid build parameters/operations')
        common = {'distance_range_m', 'height_range_m', 'yaw_candidates_rad'}
        specific = {'case1': set(), 'case2': {'handle', 'desired_direction_world', 'direction_tolerance_rad'},
                    'case3': {'obstacle', 'obstacle_asset', 'approach_offset_m', 'obstacle_distance_range_m'},
                    'case1.5': {'frame_body', 'side_a', 'side_b', 'min_distance_difference_m', 'clearance_radius_m', 'min_clearance_difference_m'}}
        if set(self.parameters) - common - specific[self.case_type]:
            raise ValueError('unknown template parameters')
        for name in ('distance_range_m', 'height_range_m', 'obstacle_distance_range_m'):
            if name in self.parameters:
                v = self.parameters[name]
                if not isinstance(v, list) or len(v) != 2:
                    raise ValueError(f'invalid {name}')
                for x in v: number(x, name)
                if not 0 <= v[0] <= v[1]: raise ValueError(f'invalid {name}')
        required = {'case1': {'distance_range_m'}, 'case2': {'handle', 'desired_direction_world'},
                    'case3': {'obstacle', 'approach_offset_m', 'obstacle_distance_range_m'},
                    'case1.5': {'frame_body', 'side_a', 'side_b', 'min_distance_difference_m', 'clearance_radius_m', 'min_clearance_difference_m'}}
        if required[self.case_type] - set(self.parameters):
            raise ValueError(f'missing {self.case_type} parameters')
        for name in ('desired_direction_world', 'approach_offset_m', 'side_a', 'side_b'):
            if name in self.parameters:
                v = self.parameters[name]
                if not isinstance(v, list) or len(v) != 3: raise ValueError(f'invalid {name}')
                for x in v: number(x, name)
                if name != 'approach_offset_m' and sum(x*x for x in v) < 1e-12:
                    raise ValueError('zero direction/side vector')
        for name in ('direction_tolerance_rad', 'min_distance_difference_m', 'clearance_radius_m', 'min_clearance_difference_m'):
            if name in self.parameters: number(self.parameters[name], name, True)
        yaws = self.parameters.get('yaw_candidates_rad', [0, 1.57079632679, 3.14159265359, -1.57079632679])
        if not isinstance(yaws, list) or not yaws: raise ValueError('invalid yaw candidates')
        for yaw in yaws: number(yaw, 'yaw')
        for name in ('obstacle','obstacle_asset','frame_body'):
            if name in self.parameters and (not isinstance(self.parameters[name],str) or not self.parameters[name]): raise ValueError(f'invalid {name}')
        if self.case_type == 'case2':
            handle = self.parameters['handle']
            if not isinstance(handle, dict) or set(handle) != {'body', 'axis_local', 'source', 'verified'} or handle['verified'] is not True or not handle['source']:
                raise ValueError('reliable, sourced handle annotation required')
            if not isinstance(handle['body'], str) or not isinstance(handle['axis_local'], list) or len(handle['axis_local']) != 3:
                raise ValueError('invalid handle annotation')
            for x in handle['axis_local']: number(x, 'handle axis')
            if sum(x*x for x in handle['axis_local']) < 1e-12: raise ValueError('zero handle axis')


def load_build_config(path):
    path = Path(path).resolve()
    value = read_json(path)
    value['protocol'] = construct(BuildProtocol, value.get('protocol', {}))
    value['budget'] = construct(BuildBudget, value.get('budget', {}))
    if value.get('asset_pool'):
        value['asset_pool'] = str((path.parent / value['asset_pool']).resolve())
    return construct(BuildConfig, value)
