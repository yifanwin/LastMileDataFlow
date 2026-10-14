"""Strict, independent configuration for procedural obstacle augmentation."""
from dataclasses import dataclass, field
from pathlib import Path
import math

from ..config import construct
from ..io import read_json
from ..recording.edit_views import ViewConfig
from ..runtime.preparation import SettleConfig
from .scene_eligibility import SUPPORT_TYPES


@dataclass(frozen=True)
class CaseConstruction:
    """Case identity plus reusable budgets, surface and asset constraints."""
    case: str = 'Case1'
    rule_version: str = 'obstacle-cases-v1'
    min_prefilter_points: int = 5
    max_final_stations: int = 20
    min_final_points: int = 1
    station_selection: str = 'coverage'
    surface_spacing_m: float = .055
    surface_alignment_jitter_rad: float = .12
    near_target_m: float = .85
    substantial_categories: list = field(default_factory=lambda: ['Kettle', 'Toaster', 'CoffeeMachine', 'DeskLamp', 'Vase'])
    min_substantial: int = 2
    substantial_height_m: float = .20
    substantial_width_m: float = .25
    registered_assets_only: bool = True
    case1_support_types: list = field(default_factory=lambda: list(SUPPORT_TYPES))

    def __post_init__(self):
        if self.case not in ('Case1', 'Case2'):
            raise ValueError('unimplemented construction.case: ' + self.case)
        if self.rule_version not in ('obstacle-cases-v1', 'scene-cases-v2', 'scene-cases-v3'):
            raise ValueError('unknown case rule version')
        if self.station_selection not in ('coverage', 'all_valid'):
            raise ValueError('unknown station selection')
        for key in ('min_prefilter_points', 'max_final_stations', 'min_substantial', 'min_final_points'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer required: ' + key)
        for key in ('surface_spacing_m', 'surface_alignment_jitter_rad', 'near_target_m', 'substantial_height_m', 'substantial_width_m'):
            if not math.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError('invalid ' + key)
        if not self.substantial_categories or any(not isinstance(c, str) for c in self.substantial_categories):
            raise ValueError('invalid substantial_categories')
        if type(self.registered_assets_only) is not bool:
            raise ValueError('registered_assets_only must be boolean')
        if not self.case1_support_types or set(self.case1_support_types)-set(SUPPORT_TYPES):
            raise ValueError('Case1 supports must be countertops/tables')


@dataclass(frozen=True)
class ObstacleConfig:
    baseline_config: str
    dataset_dir: str
    asset_library: str
    target_instance: str
    input_snapshot: str | None = None
    house: int = 103
    schema_version: str = 'procedural-obstacles-v1'
    variant_count: int = 5
    seed: int = 20261011
    obstacle_count: list = field(default_factory=lambda: [1, 1])
    mandatory_categories: list = field(default_factory=lambda: ['Chair'])
    ground_categories: list = field(default_factory=lambda: ['Chair'])
    surface_categories: list = field(default_factory=list)
    assets_per_category: int = 6
    max_candidates: int = 64
    max_physical_trials: int = 12
    variant_timeout_s: float = 900.
    yaw_jitter_rad: float = math.pi / 4
    placement_gap_m: float = .003
    collision_tolerance_m: float = .001
    source_effect_tolerance_m: float = .006
    source_effect_rotation_rad: float = .04
    support_contact_tolerance_m: float = .008
    max_tilt_rad: float = .10
    corridor_band_m: float = .12
    settle: dict = field(default_factory=lambda: {'require_source_stability': False})
    views: dict = field(default_factory=lambda: {'width': 640, 'height': 480})
    construction: dict = field(default_factory=dict)

    record_views: bool = False

    def __post_init__(self):
        if self.schema_version not in ('procedural-obstacles-v1', 'procedural-obstacles-v2', 'procedural-obstacles-v3'):
            raise ValueError('unknown procedural obstacle schema')
        for key in ('variant_count', 'assets_per_category', 'max_candidates', 'max_physical_trials'):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError('positive integer required: ' + key)
        for key in ('seed', 'house'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 0:
                raise ValueError('nonnegative integer required: ' + key)
        policy = construct(CaseConstruction, self.construction) if self.construction else None
        modern = self.schema_version != 'procedural-obstacles-v1'
        versions = {'procedural-obstacles-v1': 'obstacle-cases-v1', 'procedural-obstacles-v2': 'scene-cases-v2', 'procedural-obstacles-v3': 'scene-cases-v3'}
        if policy and policy.rule_version != versions[self.schema_version] or modern and policy is None:
            raise ValueError('schema and case rule version mismatch')
        lo, hi = ((10, 20) if policy.case == 'Case2' else (6, 9)) if modern else (
            (6, 12) if policy and policy.case == 'Case2' else (1, 3))
        if (len(self.obstacle_count) != 2 or any(type(x) is not int for x in self.obstacle_count)
                or not lo <= self.obstacle_count[0] <= self.obstacle_count[1] <= hi):
            raise ValueError(f'obstacle_count must be an inclusive range within {lo}..{hi}')
        if policy and policy.case == 'Case1' and self.mandatory_categories != ['Chair']:
            raise ValueError('Case1 requires exactly one mandatory Chair')
        if modern and policy.case == 'Case1' and (self.ground_categories != ['Chair'] or 'Chair' in self.surface_categories):
            raise ValueError('Case1 requires one ground Chair and surface-only remainder')
        if policy and policy.case == 'Case2':
            if self.ground_categories or self.mandatory_categories:
                raise ValueError('Case2 is surface-only; uses measured substantial assets')
            if policy.min_substantial > self.obstacle_count[0]:
                raise ValueError('substantial count exceeds minimum count')
        for key in ('mandatory_categories', 'ground_categories', 'surface_categories'):
            values = getattr(self, key)
            if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values) or len(set(values)) != len(values):
                raise ValueError('invalid categories: ' + key)
        if not self.ground_categories and not self.surface_categories:
            raise ValueError('empty obstacle pool')
        if not set(self.mandatory_categories) <= set(self.ground_categories + self.surface_categories):
            raise ValueError('mandatory categories must be in the pool')
        if len(self.mandatory_categories) > self.obstacle_count[0]:
            raise ValueError('mandatory categories exceed minimum count')
        for key in ('variant_timeout_s', 'yaw_jitter_rad', 'placement_gap_m', 'collision_tolerance_m',
                    'source_effect_tolerance_m', 'source_effect_rotation_rad',
                    'support_contact_tolerance_m', 'max_tilt_rad', 'corridor_band_m'):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid ' + key)
        for key in ('baseline_config', 'dataset_dir', 'asset_library', 'target_instance'):
            if not isinstance(getattr(self, key), str) or not getattr(self, key):
                raise ValueError('nonempty string required: ' + key)
        construct(SettleConfig, self.settle)
        construct(ViewConfig, self.views)

    @property
    def case_policy(self):
        return construct(CaseConstruction, self.construction) if self.construction else None


def load_obstacle_config(path):
    path = Path(path).resolve()
    values = read_json(path)
    for key in ('baseline_config', 'dataset_dir', 'asset_library', 'input_snapshot'):
        if values.get(key):
            values[key] = str((path.parent / values[key]).resolve())
    return construct(ObstacleConfig, values)
