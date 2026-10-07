"""Strict v0.2 single-scene requests; v0.1 editing contracts remain unchanged."""
from dataclasses import dataclass, field
from pathlib import Path
import re

from .case_schema import (CaseTemplate, JsonContract, bounds, nested, number,
                          require, strings, text)
from ..scenes.source import SceneSource


@dataclass(frozen=True)
class SceneInput(JsonContract):
    kind: str
    scene_id: str | None = None
    dataset_dir: str | None = None
    xml_path: str | None = None
    metadata_path: str | None = None

    def __post_init__(self):
        require(self.kind in ('single', 'dataset'), 'scene_input.kind', 'single or dataset required')
        for name in ('scene_id', 'dataset_dir', 'xml_path', 'metadata_path'):
            if getattr(self, name) is not None:
                text(getattr(self, name), 'scene_input.' + name)
        if self.kind == 'single':
            require(bool(self.xml_path) != bool(self.dataset_dir), 'scene_input', 'XML OR dataset_dir required')
            text(self.scene_id, 'scene_input.scene_id')
            require(not self.metadata_path or self.xml_path, 'metadata_path', 'only for explicit XML')
        else:
            require(bool(self.dataset_dir) and not any((self.scene_id, self.xml_path, self.metadata_path)),
                    'scene_input', 'dataset requires only dataset_dir')

    def resolve(self):
        if self.kind != 'single':
            raise NotImplementedError('dataset_scene_retrieval_not_implemented; specify kind=single')
        if self.xml_path:
            source = SceneSource(self.scene_id, str(Path(self.xml_path).resolve()),
                                 str(Path(self.metadata_path).resolve()) if self.metadata_path else None)
        else:
            match = re.fullmatch(r'(train|val|test)[_-](\d+)', self.scene_id)
            require(match is not None, 'scene_id', 'expected train/val/test-<house>')
            source = SceneSource.procthor(self.dataset_dir, int(match[2]))
            require(source.split == match[1], 'scene_id', 'split disagrees with dataset directory')
        for name in ('xml_path', 'metadata_path'):
            if getattr(source, name) and not Path(getattr(source, name)).is_file():
                raise FileNotFoundError(getattr(source, name))
        return source


@dataclass(frozen=True)
class ConstructionBudget(JsonContract):
    max_rounds: int = 4
    proposals_per_round: int = 3
    samples_per_proposal: int = 8
    max_samples: int = 64
    max_agent_calls: int = 32
    timeout_s: float = 900.
    agent_timeout_s: float = 120.

    def __post_init__(self):
        for name in ('max_rounds', 'proposals_per_round', 'samples_per_proposal', 'max_samples', 'max_agent_calls'):
            require(type(getattr(self, name)) is int and getattr(self, name) >= 0, name, 'nonnegative integer required')
        number(self.timeout_s, 'timeout_s', positive=True)
        number(self.agent_timeout_s, 'agent_timeout_s', positive=True)


@dataclass(frozen=True)
class LocalConstructionConfig(JsonContract):
    radius_m: float = 2.5
    max_radius_m: float = 4.5
    max_contexts: int = 8
    max_nodes: int = 240
    max_initialization_trials: int = 96
    initialization_candidates: int = 3
    max_information_requests: int = 2
    observation_distance_m: list = field(default_factory=lambda: [.8, 2.2])
    min_target_pixels: int = 24
    assets_per_category: int = 3
    asset_library: str | None = None

    def __post_init__(self):
        for name in ('radius_m', 'max_radius_m'):
            number(getattr(self, name), name, positive=True)
        require(self.radius_m <= self.max_radius_m, 'max_radius_m', 'smaller than initial radius')
        for name in ('max_contexts', 'max_nodes', 'max_initialization_trials', 'initialization_candidates', 'min_target_pixels', 'assets_per_category'):
            require(type(getattr(self, name)) is int and getattr(self, name) > 0, name, 'positive integer required')
        require(type(self.max_information_requests) is int and 0 <= self.max_information_requests <= 5,
                'max_information_requests', 'integer in [0,5] required')
        bounds(self.observation_distance_m, 'observation_distance_m')
        require(self.observation_distance_m[0] > 0, 'observation_distance_m', 'positive distance required')
        if self.asset_library is not None:
            text(self.asset_library, 'asset_library')


@dataclass(frozen=True)
class SceneConstructionRequest(JsonContract):
    case_description: str
    scene_input: SceneInput
    case_type: str
    task_type: str = 'pick'
    objective_mode: str = 'beneficial_reposition'
    verification_mode: str = 'construction_only'
    target_variant_count: int = 1
    seed: int = 42
    budgets: ConstructionBudget = field(default_factory=ConstructionBudget)
    construction: LocalConstructionConfig = field(default_factory=LocalConstructionConfig)
    difficulty_profile: dict | None = None
    request_version: str = '0.2'

    def __post_init__(self):
        require(self.request_version == '0.2', 'request_version', 'expected 0.2')
        text(self.case_description, 'case_description')
        require(self.case_type in ('case1', 'case1.5', 'case3'), 'case_type', 'v0.2 scope is case1/case1.5/case3')
        require(self.task_type == 'pick', 'task_type', 'v0.2 implements pick construction only')
        require(self.objective_mode in ('beneficial_reposition', 'required_reposition'), 'objective_mode', 'unknown objective')
        require(self.verification_mode in ('construction_only', 'mobile_task'), 'verification_mode', 'unknown verification mode')
        require(type(self.target_variant_count) is int and self.target_variant_count > 0, 'target_variant_count', 'positive integer required')
        require(type(self.seed) is int and self.seed >= 0, 'seed', 'nonnegative integer required')
        for name, cls in (('scene_input', SceneInput), ('construction', LocalConstructionConfig), ('budgets', ConstructionBudget)):
            object.__setattr__(self, name, nested(cls, getattr(self, name), name))
        require(self.difficulty_profile is None or isinstance(self.difficulty_profile, dict), 'difficulty_profile', 'object required')


@dataclass(frozen=True)
class MobileCaseTemplate(CaseTemplate):
    """Extends the checked rule template; never feeds new predicates to v0.1 rules."""
    task_type: str = 'pick'
    objective_mode: str = 'beneficial_reposition'
    task_hypotheses: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        require(self.task_type == 'pick', 'task_type', 'pick required')
        require(self.objective_mode in ('beneficial_reposition', 'required_reposition'), 'objective_mode', 'unknown objective')
        strings(self.task_hypotheses, 'task_hypotheses')
        strings(self.assumptions, 'assumptions')
        require(bool(self.semantic_checks) and bool(self.task_hypotheses), 'template', 'visible checks AND task hypotheses required')


def load_scene_request(path):
    from ..io import read_json
    path = Path(path).resolve()
    value = read_json(path)
    for name in ('dataset_dir', 'xml_path', 'metadata_path'):
        if value.get('scene_input', {}).get(name):
            value['scene_input'][name] = str((path.parent/value['scene_input'][name]).resolve())
    if value.get('construction', {}).get('asset_library'):
        value['construction']['asset_library'] = str((path.parent/value['construction']['asset_library']).resolve())
    return SceneConstructionRequest.from_dict(value)
