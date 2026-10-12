"""Strategy-free scene recipes and robot/sensor station geometry."""
from dataclasses import dataclass, field
from pathlib import Path
import math
import re

from ..config import RobotConfig, construct
from ..io import read_json
from .scene_eligibility import PICK_TYPES, SUPPORT_TYPES


def house_number(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        match = re.fullmatch(r'(?:val_?)?(\d+)', value)
        if match:
            return int(match[1])
    raise ValueError('invalid val house identity')


@dataclass(frozen=True)
class SceneStationConfig:
    radius_m: float = .7928056080178096
    spacing_m: float = .1108796040001314
    width: int = 320
    height: int = 240
    min_target_pixels: int = 24
    map_resolution_m: float = .05
    navigation_margin_m: float = .02
    seed: int = 20261012
    operations: list = field(default_factory=lambda: ['pick', 'open'])
    gripper_width_m: float = .10
    open_fraction: float = .3
    open_hinge_rad: float = math.pi / 6
    open_slide_m: float = .10

    def __post_init__(self):
        for key in ('radius_m', 'spacing_m', 'map_resolution_m', 'gripper_width_m',
                    'open_fraction', 'open_hinge_rad', 'open_slide_m'):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid station geometry: ' + key)
        if not 0 < self.open_fraction <= 1:
            raise ValueError('invalid open_fraction')
        if not math.isfinite(self.navigation_margin_m) or self.navigation_margin_m < 0:
            raise ValueError('invalid navigation margin')
        for key in ('width', 'height', 'min_target_pixels'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer required: ' + key)
        if not self.operations or set(self.operations) - {'pick', 'open'}:
            raise ValueError('unsupported operations')
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError('invalid seed')


@dataclass(frozen=True)
class SceneDatasetRecipe:
    dataset_dir: str
    asset_library: str
    robot_config: str
    houses: list
    schema_version: str = 'scene-dataset-recipe-v1'
    rule_version: str = 'scene-cases-v2'
    targets: dict = field(default_factory=dict)
    input_snapshots: dict = field(default_factory=dict)
    categories: list = field(default_factory=list)
    pick_target_types: list = field(default_factory=list)
    case1_support_types: list = field(default_factory=lambda: list(SUPPORT_TYPES))
    allow_surface_capacity_shortfall: bool = False
    capacity_probe_budget: int = 20000
    cases: list = field(default_factory=lambda: ['Case1', 'Case2', 'Case3'])
    variants_per_case: int = 5
    max_pick_targets_per_house: int = 3
    max_open_targets_per_house: int = 1
    min_target_separation_m: float = .75
    min_prefilter_points: int = 5
    min_final_points: int = 5
    max_final_stations: int = 20
    station_selection: str = 'coverage'
    station_config: dict = field(default_factory=dict)
    seed: int = 20261012
    assets_per_category: int = 6
    max_candidates: int = 2400
    max_physical_trials: int = 12
    variant_timeout_s: float = 1800.
    case1_added_surface_count: list = field(default_factory=lambda: [5, 8])
    case2_added_surface_count: list = field(default_factory=lambda: [10, 20])
    case1_surface_categories: list = field(default_factory=lambda: ['Bowl', 'Cup', 'Mug', 'Bottle', 'Plate'])
    case2_surface_categories: list = field(default_factory=lambda: ['Bowl', 'Cup', 'Mug', 'Bottle', 'SaltShaker', 'Kettle', 'CoffeeMachine', 'Vase'])
    min_substantial: int = 2
    settle: dict = field(default_factory=lambda: {'require_source_stability': False})
    views: dict = field(default_factory=lambda: {'width': 640, 'height': 480})

    def __post_init__(self):
        if self.schema_version != 'scene-dataset-recipe-v1' or self.rule_version not in ('scene-cases-v2', 'scene-cases-v3'):
            raise ValueError('unknown scene dataset recipe/rule version')
        if type(self.allow_surface_capacity_shortfall) is not bool or (self.allow_surface_capacity_shortfall and self.rule_version != 'scene-cases-v3'):
            raise ValueError('capacity shortfall requires scene-cases-v3')
        if self.rule_version == 'scene-cases-v3' and (not self.pick_target_types or set(self.pick_target_types)-set(PICK_TYPES)):
            raise ValueError('v3 requires explicit approved pick target types')
        if not self.case1_support_types or set(self.case1_support_types)-set(SUPPORT_TYPES):
            raise ValueError('Case1 supports must be countertops/tables')
        normalized = [house_number(h) for h in self.houses]
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError('explicit, unique, nonempty houses required; no implicit full scan')
        object.__setattr__(self, 'houses', normalized)
        if not self.cases or set(self.cases) - {'Case1', 'Case2', 'Case3'} or len(set(self.cases)) != len(self.cases):
            raise ValueError('invalid cases')
        if self.station_selection not in ('coverage', 'all_valid'):
            raise ValueError('invalid station selection')
        for key in ('max_pick_targets_per_house', 'max_open_targets_per_house'):
            value = getattr(self, key)
            if type(value) is not int or (value != -1 and value <= 0):
                raise ValueError('target count must be -1 (unlimited) or a positive integer: ' + key)
        for key in ('variants_per_case',
                    'min_prefilter_points', 'min_final_points', 'max_final_stations', 'assets_per_category',
                    'max_candidates', 'max_physical_trials', 'min_substantial', 'capacity_probe_budget'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer required: ' + key)
        if self.station_selection == 'coverage' and self.min_final_points > self.max_final_stations:
            raise ValueError('final station limit below publication minimum')
        for name, bounds in (('case1_added_surface_count', (5, 8)), ('case2_added_surface_count', (10, 20))):
            values = getattr(self, name)
            if not isinstance(values, list) or len(values) != 2 or any(type(v) is not int for v in values) or not bounds[0] <= values[0] <= values[1] <= bounds[1]:
                raise ValueError('invalid ' + name)
        if not isinstance(self.targets, dict) or any(not isinstance(v, list) or any(not isinstance(t, str) for t in v) for v in self.targets.values()):
            raise ValueError('targets must map house identities to explicit selector lists')
        if any(house_number(k) not in normalized for k in self.targets):
            raise ValueError('target selectors reference unrequested house')
        if type(self.seed) is not int or self.seed < 0 or not math.isfinite(self.variant_timeout_s) or self.variant_timeout_s <= 0:
            raise ValueError('invalid seed/time budget')
        if not math.isfinite(self.min_target_separation_m) or self.min_target_separation_m <= 0:
            raise ValueError('invalid target separation')
        for name in ('categories', 'pick_target_types', 'case1_support_types', 'case1_surface_categories', 'case2_surface_categories'):
            values = getattr(self, name)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values) or len(set(values)) != len(values):
                raise ValueError('invalid ' + name)
        self.geometry

    @property
    def geometry(self):
        return construct(SceneStationConfig, {**self.station_config, 'seed': self.seed})

    def selectors(self, house):
        return [t for h, values in self.targets.items() if house_number(h) == house for t in values]


def load_scene_recipe(path):
    path = Path(path).resolve()
    values = read_json(path)
    for key in ('dataset_dir', 'asset_library', 'robot_config'):
        values[key] = str((path.parent / values[key]).resolve())
    if values.get('input_snapshots'):
        values['input_snapshots'] = {str(house_number(k)): str((path.parent / v).resolve()) for k, v in values['input_snapshots'].items()}
    return construct(SceneDatasetRecipe, values)


def load_scene_robot(path):
    path = Path(path).resolve()
    values = read_json(path)
    values['model_path'] = str((path.parent / values['model_path']).resolve())
    return construct(RobotConfig, values)
