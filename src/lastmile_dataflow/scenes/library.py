"""Scene recipes, station geometry independent of robot dimensions, and accepted scene records."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import numpy as np

from lastmile_dataflow.utils.io import read_config
from lastmile_dataflow.robots.registry import load_robot_config
from lastmile_dataflow.utils.io import rows
from lastmile_dataflow.robots.panda_omron.robocasa import ROBOCASA_CAMERAS
from lastmile_dataflow.sim.simulation import Simulation


def house_number(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        match = re.fullmatch(r"(?:val_?)?(\d+)", value)
        if match:
            return int(match[1])
    raise ValueError("invalid val house identity")


@dataclass(frozen=True)
class SceneStationConfig:
    radius_m: float = 1.0
    spacing_m: float = 0.25
    width: int = 320
    height: int = 240
    min_target_pixels: int = 24
    min_reference_pixels: int = 24
    map_resolution_m: float = 0.05
    navigation_margin_m: float = 0.02
    navigation_whole_body: bool = False
    max_pick_object_size_m: float | None = None
    seed: int = 42
    operations: list = field(default_factory=lambda: ["pick", "open"])
    gripper_width_m: float = 0.10
    open_fraction: float = 0.3
    open_hinge_rad: float = math.pi / 6
    open_slide_m: float = 0.10

    def __post_init__(self):
        for key in (
            "radius_m",
            "spacing_m",
            "map_resolution_m",
            "gripper_width_m",
            "open_fraction",
            "open_hinge_rad",
            "open_slide_m",
        ):
            value = getattr(self, key)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("invalid station geometry: " + key)
        if not 0 < self.open_fraction <= 1:
            raise ValueError("invalid open_fraction")
        if not math.isfinite(self.navigation_margin_m) or self.navigation_margin_m < 0:
            raise ValueError("invalid navigation margin")
        for key in ("width", "height", "min_target_pixels", "min_reference_pixels"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError("positive integer required: " + key)
        if not self.operations or set(self.operations) - {"pick", "open"}:
            raise ValueError("unsupported operations")
        if self.seed < 0:
            raise ValueError("invalid seed")


@dataclass(frozen=True)
class SceneEditConfig:
    enabled: bool = True
    tabletop_count: tuple[int, int] = (1, 3)
    floor_count: tuple[int, int] = (0, 2)
    tabletop_radius_m: float = 0.3
    max_proposals: int = 32
    asset_shortlist: int = 5
    variant_timeout_s: float = 1800.0

    def __post_init__(self):
        for limits in (self.tabletop_count, self.floor_count):
            if len(limits) != 2 or not 0 <= limits[0] <= limits[1]:
                raise ValueError("obstacle count must be a nonnegative min/max pair")
        if min(self.tabletop_radius_m, self.max_proposals, self.asset_shortlist, self.variant_timeout_s) <= 0:
            raise ValueError("positive edit geometry and budgets required")


@dataclass(frozen=True)
class SceneRecipe:
    dataset_dir: str
    assets_dir: str
    asset_index: str
    robot_config: str
    houses: list
    schema_version: str = "scene-recipe-v1"
    edits: SceneEditConfig = field(default_factory=SceneEditConfig)
    operations: list = field(default_factory=lambda: ["pick", "open"])
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
    train_fraction: float = 0.8
    seed: int = 42
    task_timeout_s: float = 300.0
    build_workers: int = 1
    settle: dict = field(default_factory=lambda: {"require_source_stability": False})

    def __post_init__(self):
        if self.schema_version != "scene-recipe-v1":
            raise ValueError("expected scene-recipe-v1")
        if isinstance(self.edits, dict):
            object.__setattr__(self, "edits", SceneEditConfig(**self.edits))
        houses = [house_number(h) for h in self.houses]
        if not houses or len(set(houses)) != len(houses):
            raise ValueError("explicit unique source houses required")
        object.__setattr__(self, "houses", houses)
        if not isinstance(self.exclude_categories, list) or any(
            not isinstance(category, str) or not category for category in self.exclude_categories
        ):
            raise ValueError("invalid excluded target categories")
        if not isinstance(self.include_categories, list) or any(
            not isinstance(category, str) or not category for category in self.include_categories
        ):
            raise ValueError("invalid included target categories")
        if not isinstance(self.include_assets, dict) or any(
            not isinstance(category, str)
            or not category
            or not isinstance(prefixes, list)
            or any(not isinstance(prefix, str) or not prefix for prefix in prefixes)
            for category, prefixes in self.include_assets.items()
        ):
            raise ValueError("invalid included target assets")
        if not isinstance(self.exclude_tasks, list) or any(
            not isinstance(task, str) or ":" not in task for task in self.exclude_tasks
        ):
            raise ValueError("invalid excluded house:task entries")
        if not self.operations or set(self.operations) - {"pick", "open"}:
            raise ValueError("task modes support Pick/Open")
        if self.geometry.radius_m != 1.0 or self.geometry.spacing_m != 0.25:
            raise ValueError("task-mode sampling requires 1 m / 0.25 m")
        if type(self.max_final_stations) is not int or not 1 <= self.max_final_stations <= 20:
            raise ValueError("task-mode pose budget is at most twenty, including yaw variants")
        for key in ("max_pick_targets_per_house", "max_open_targets_per_house"):
            if type(getattr(self, key)) is not int or getattr(self, key) == 0 or getattr(self, key) < -1:
                raise ValueError("target limit is -1 or positive: " + key)
        if self.seed < 0 or self.split_seed < 0 or not 0 <= self.train_fraction <= 1:
            raise ValueError("invalid split or random seed")
        if not math.isfinite(self.task_timeout_s) or self.task_timeout_s <= 0:
            raise ValueError("invalid task timeout")
        splits = {str(house_number(h)): split for h, split in self.house_splits.items()}
        if not splits:
            order = np.random.default_rng(self.split_seed).permutation(sorted(houses))
            train = set(order[: int(len(order) * self.train_fraction)])
            splits = {str(h): "train" if h in train else "val" for h in sorted(houses)}
        if not {str(h) for h in houses} <= splits.keys() or set(splits.values()) - {"train", "val"}:
            raise ValueError("source split must cover every requested house with train/val")
        object.__setattr__(self, "house_splits", splits)

    @property
    def geometry(self):
        return SceneStationConfig(**{**self.station_config, "seed": self.seed, "operations": self.operations})

    def selectors(self, house):
        return [t for h, values in self.targets.items() if house_number(h) == house for t in values]

    def selects(self, task, house):
        """Explicit per-house targets (instance, asset or task id); no targets selects every task."""
        selectors = self.selectors(house)
        return not selectors or any(
            selector in (task["instance_id"], task["asset_id"], task["task_id"]) for selector in selectors
        )

    def rejection(self, task, house):
        """Why the recipe's category/asset/task filters exclude ``task``, or None."""
        if f"{house}:{task['task_id']}" in self.exclude_tasks:
            return "excluded_task"
        if self.include_categories and task["category"] not in self.include_categories:
            return "not_included_target_category"
        prefixes = self.include_assets.get(task["category"])
        if prefixes and not any(str(task.get("asset_id", "")).startswith(prefix) for prefix in prefixes):
            return "not_included_target_asset"
        if task["category"] in self.exclude_categories:
            return "excluded_target_category"
        return None


def load_scene_recipe(path):
    recipe = SceneRecipe(
        **read_config(
            path,
            data_fields=("dataset_dir", "assets_dir", "asset_index", "input_snapshots"),
            repo_fields=("robot_config",),
        )
    )
    robot = load_robot_config(recipe.robot_config)
    if robot.control_profile == "robocasa-v1":
        if set(robot.cameras) != set(ROBOCASA_CAMERAS):
            raise ValueError("RoboCasa scenes require all three cameras")
    return recipe


STATIONS = "stations.jsonl"


def scene_records(dataset):
    """Every scenes.jsonl row with its sampled stations merged; a scene without legal stations is rejected."""
    root = Path(dataset).resolve()
    path = root / STATIONS
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run scripts/sample_stations.py on this scene library first")
    stations = {row["scene_id"]: row for row in rows(path)}
    records = []
    for record in rows(root / "scenes.jsonl"):
        sampled = stations.get(record.get("scene_id"))
        if record["status"] == "accepted" and sampled is not None:
            record = {**record, **sampled}
        records.append(record)
    return records


def load_scenes(dataset, samples=None):
    root = Path(dataset).resolve()
    records = [r for r in scene_records(root) if r["status"] == "accepted"]
    if samples and set(samples) - {r["scene_id"] for r in records}:
        raise ValueError("requested scene not found")
    return [{**r, "scene_path": str(root / r["scene"])} for r in records if not samples or r["scene_id"] in samples]


@contextmanager
def open_scene(scene):
    """The library snapshot of ``scene`` (with ``robot_config`` resolved), closed on exit."""
    sim = Simulation.from_snapshot(
        scene["scene_path"], scene["robot_config"], target=scene["task"]["target_body"], scene_id=scene["scene_id"]
    )
    try:
        yield sim
    finally:
        sim.close()
