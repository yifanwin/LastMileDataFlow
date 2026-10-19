"""Strategy-free scene recipes and robot/sensor station geometry."""
from dataclasses import dataclass, field
import math
import re

from ..config import load_robot_config, read_config
from .tasks.eligibility import PICK_TYPES, SUPPORT_TYPES


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
    radius_m: float
    spacing_m: float
    require_target_visibility: bool = False
    require_navigation_clearance: bool = False
    width: int = 320
    height: int = 240
    min_target_pixels: int = 24
    map_resolution_m: float = .05
    navigation_margin_m: float = .02
    navigation_whole_body: bool = False
    max_pick_object_size_m: float | None = None
    seed: int = 42
    operations: list = field(default_factory=lambda: ['pick', 'open'])
    gripper_width_m: float = .10
    open_fraction: float = .3
    open_hinge_rad: float = math.pi / 6
    open_slide_m: float = .10

    @classmethod
    def from_robot(cls, robot, **values):
        values.setdefault('radius_m', robot.arm_working_reach_m)
        if 'spacing_m' not in values:
            values['spacing_m'] = robot.base_radius_m / 4 if robot.base_radius_m is not None else None
        if values['radius_m'] is None or values['spacing_m'] is None:
            raise ValueError('configure robot arm_working_reach_m and base_radius_m, or explicit sampling geometry')
        return cls(**values)

    def __post_init__(self):
        for key in ('radius_m', 'spacing_m', 'map_resolution_m', 'gripper_width_m',
                    'open_fraction', 'open_hinge_rad', 'open_slide_m'):
            value = getattr(self, key)
            if not math.isfinite(value) or value <= 0:
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
        if self.seed < 0:
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
    seed: int = 42
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
                    'max_candidates', 'max_physical_trials', 'min_substantial'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer required: ' + key)
        if self.station_selection == 'coverage' and self.min_final_points > self.max_final_stations:
            raise ValueError('final station limit below minimum count')
        for name, bounds in (('case1_added_surface_count', (5, 8)), ('case2_added_surface_count', (10, 20))):
            values = getattr(self, name)
            if len(values) != 2 or not bounds[0] <= values[0] <= values[1] <= bounds[1]:
                raise ValueError('invalid ' + name)
        if any(house_number(k) not in normalized for k in self.targets):
            raise ValueError('target selectors reference unrequested house')
        if self.seed < 0 or not math.isfinite(self.variant_timeout_s) or self.variant_timeout_s <= 0:
            raise ValueError('invalid seed/time budget')
        if not math.isfinite(self.min_target_separation_m) or self.min_target_separation_m <= 0:
            raise ValueError('invalid target separation')

    @property
    def geometry(self):
        values = {**self.station_config, 'seed': self.seed}
        if 'radius_m' in values and 'spacing_m' in values:
            return SceneStationConfig(**values)
        return SceneStationConfig.from_robot(load_robot_config(self.robot_config), **values)

    def selectors(self, house):
        return [t for h, values in self.targets.items() if house_number(h) == house for t in values]


def load_scene_recipe(path):
    return SceneDatasetRecipe(**read_config(path, data_fields=('dataset_dir', 'asset_library', 'input_snapshots'), repo_fields=('robot_config',)))


def load_scene_robot(path):
    return load_robot_config(path)
