"""Scene recipes and station geometry independent of robot dimensions."""
from dataclasses import dataclass, field
import math
import re
import numpy as np

from ..config import load_robot_config, read_config
from .mode_config import MODE_NAMES, load_mode_config


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
    radius_m: float = 1.0
    spacing_m: float = .25
    require_target_visibility: bool = False
    require_navigation_clearance: bool = False
    width: int = 320
    height: int = 240
    min_target_pixels: int = 24
    min_reference_pixels: int = 24
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
        for key in ('width', 'height', 'min_target_pixels', 'min_reference_pixels'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer required: ' + key)
        if not self.operations or set(self.operations) - {'pick', 'open'}:
            raise ValueError('unsupported operations')
        if self.seed < 0:
            raise ValueError('invalid seed')


@dataclass(frozen=True)
class TaskModeRecipe:
    dataset_dir: str
    assets_dir: str
    asset_index: str
    robot_config: str
    mode_config: str
    houses: list
    schema_version: str = 'scene-dataset-recipe-v2'
    execution_config: str = 'configs/task_modes/panda_omron_calibration.json'
    modes: list = field(default_factory=lambda: list(MODE_NAMES))
    operations: list = field(default_factory=lambda: ['pick', 'open'])
    variants_per_intent: int = 1
    max_final_stations: int = 20
    max_pick_targets_per_house: int = 3
    max_open_targets_per_house: int = 1
    targets: dict = field(default_factory=dict)
    exclude_categories: list = field(default_factory=list)
    include_categories: list = field(default_factory=list)
    include_assets: dict = field(default_factory=dict)
    exclude_tasks: list = field(default_factory=list)
    input_snapshots: dict = field(default_factory=dict)
    station_config: dict = field(default_factory=dict)
    house_splits: dict = field(default_factory=dict)
    split_seed: int = 42
    train_fraction: float = .8
    seed: int = 42
    task_timeout_s: float = 300.
    local_task_models: bool = False
    settle: dict = field(default_factory=lambda: {'require_source_stability': False})

    def __post_init__(self):
        if self.schema_version != 'scene-dataset-recipe-v2':
            raise ValueError('expected task-mode recipe v2')
        houses = [house_number(h) for h in self.houses]
        if not houses or len(set(houses)) != len(houses):
            raise ValueError('explicit unique source houses required')
        object.__setattr__(self, 'houses', houses)
        if len(set(self.modes)) != len(self.modes) or set(self.modes) - set(MODE_NAMES):
            raise ValueError('unknown or duplicate task mode')
        # Empty modes = natural/no-edit dataset: freeze the raw scene and its valid stations.
        if not isinstance(self.exclude_categories, list) or any(
                not isinstance(category, str) or not category for category in self.exclude_categories):
            raise ValueError('invalid excluded target categories')
        if not isinstance(self.include_categories, list) or any(
                not isinstance(category, str) or not category for category in self.include_categories):
            raise ValueError('invalid included target categories')
        if not isinstance(self.include_assets, dict) or any(
                not isinstance(category, str) or not category or not isinstance(prefixes, list)
                or any(not isinstance(prefix, str) or not prefix for prefix in prefixes)
                for category, prefixes in self.include_assets.items()):
            raise ValueError('invalid included target assets')
        if not isinstance(self.exclude_tasks, list) or any(
                not isinstance(task, str) or ':' not in task for task in self.exclude_tasks):
            raise ValueError('invalid excluded house:task entries')
        if not self.operations or set(self.operations) - {'pick', 'open'}:
            raise ValueError('task modes support Pick/Open')
        if self.geometry.radius_m != 1. or self.geometry.spacing_m != .25:
            raise ValueError('task-mode sampling requires 1 m / 0.25 m')
        if type(self.max_final_stations) is not int or not 1 <= self.max_final_stations <= 20:
            raise ValueError('task-mode pose budget is at most twenty, including yaw variants')
        if type(self.variants_per_intent) is not int or self.variants_per_intent < 1:
            raise ValueError('positive variants_per_intent required')
        if self.local_task_models:
            raise ValueError('initial task-mode validation uses complete source models')
        for key in ('max_pick_targets_per_house', 'max_open_targets_per_house'):
            if type(getattr(self, key)) is not int or getattr(self, key) == 0 or getattr(self, key) < -1:
                raise ValueError('target limit is -1 or positive: ' + key)
        if self.seed < 0 or self.split_seed < 0 or not 0 <= self.train_fraction <= 1:
            raise ValueError('invalid split or random seed')
        if not math.isfinite(self.task_timeout_s) or self.task_timeout_s <= 0:
            raise ValueError('invalid task timeout')
        splits = {str(house_number(h)): split for h, split in self.house_splits.items()}
        if not splits:
            order = np.random.default_rng(self.split_seed).permutation(sorted(houses))
            train = set(order[:int(len(order) * self.train_fraction)])
            splits = {str(h): 'train' if h in train else 'val' for h in sorted(houses)}
        if not {str(h) for h in houses} <= splits.keys() or set(splits.values()) - {'train', 'val'}:
            raise ValueError('source split must cover every requested house with train/val')
        object.__setattr__(self, 'house_splits', splits)

    @property
    def geometry(self):
        return SceneStationConfig(**{**self.station_config, 'seed': self.seed, 'operations': self.operations})

    def selectors(self, house):
        return [t for h, values in self.targets.items() if house_number(h) == house for t in values]


def load_task_mode_recipe(path):
    recipe = TaskModeRecipe(**read_config(path,
        data_fields=('dataset_dir', 'assets_dir', 'asset_index', 'input_snapshots'),
        repo_fields=('robot_config', 'mode_config', 'execution_config')))
    robot = load_robot_config(recipe.robot_config)
    if robot.name != 'panda_omron' or robot.control_profile != 'robocasa-v1':
        raise ValueError('task modes require PandaOmron robocasa-v1')
    from ..runtime.control.panda_robocasa import ROBOCASA_CAMERAS
    if set(robot.cameras) != set(ROBOCASA_CAMERAS):
        raise ValueError('task modes require all three RoboCasa cameras')
    load_mode_config(recipe.mode_config)
    return recipe
