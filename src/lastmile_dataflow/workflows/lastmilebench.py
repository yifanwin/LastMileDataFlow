"""Build from existing reference calibration; independently evaluate policies."""

from collections import Counter
from dataclasses import asdict, dataclass
import logging
from pathlib import Path
import time

import numpy as np

from lastmile_dataflow.robots.registry import robot_config
from ..data.evaluation.benchmark import BenchmarkConfig, load_benchmark_config
from ..data.evaluation.benchmark import reference_calibration, select_starts, failure_family
from ..data.evaluation.recovery import RecoveryRegion, difficulty
from ..planning.navigation.astar import navigation_grid
from ..planning.navigation.geometry import GeometryNavigation, NavigationSettings, NAVIGATION_PROTOCOL
from ..scenes.dataset import load_scenes, open_scene
from lastmile_dataflow.sim.placement import place_frozen_station
from ..runtime.outcomes.contacts import ContactSampler, NavigationContacts
from lastmile_dataflow.scenes.geometry import floor_support
from lastmile_dataflow.sim.placement import robot_footprint
from ..utils.io import create_run, read_json, rows, write_json, write_rows
from ..utils.logging_config import Progress
from ..data.visualization.benchmark import benchmark_view

logger = logging.getLogger(__name__)


def _navigation_region(sim, scene, calibration, settings):
    """Validate unchanged source initializations and construct one task grid."""
    observed = {r["station_id"]: r for r in calibration["stations"]}
    legal, measured, footprints = [], [], []
    rejected = Counter()
    sampler = ContactSampler(sim)
    for station in scene["stations"]:
        if station["geometry"] != "valid":
            rejected["source_initialization_invalid"] += 1
            continue
        place_frozen_station(sim, station)
        contacts = NavigationContacts()
        if contacts.observe(sim, sampler, settings.navigation_max_penetration_m) or not floor_support(
            sim, station["xy"]
        ):
            rejected["initialization_invalid"] += 1
            continue
        legal.append(station)
        footprints.append(robot_footprint(sim, whole_body=settings.navigation_whole_body))
        row = observed[station["station_id"]]
        if row["complete"]:
            measured.append(
                {
                    "station_id": station["station_id"],
                    "xy": station["xy"],
                    "yaw": station["base"][2],
                    "successes": row["successes"],
                    "trials": row["trials"],
                    "high": row["successes"] / row["trials"] >= settings.high,
                }
            )
    if not footprints:
        return None, legal, dict(rejected), None
    footprint = {
        "radius_m": max(f["radius_m"] for f in footprints),
        "height_m": max(f["max"][2] for f in footprints),
        "method": "held_whole_body_swept_circle" if settings.navigation_whole_body else "chassis_only",
    }
    grid = navigation_grid(sim, scene["task"], settings, footprint["radius_m"], footprint["height_m"])
    # Existing station visibility is reusable: construction has not changed the
    # source model, cameras, target, or initialization. No rerender is needed.
    return RecoveryRegion.build(grid, measured, settings), legal, dict(rejected), footprint


SCHEMA = "lastmile-bench-v0.1"


@dataclass(frozen=True)
class EligibleEpisode:
    station: dict
    evidence: dict
    metrics: dict
    group: dict
    region: RecoveryRegion


def _no_choice_reason(complete, low_count, high_count):
    if not complete:
        return "incomplete_calibration"
    return "no_high_success_station" if low_count and not high_count else "no_low_success_station"


def _scene_report(scene, calibration, choices, settings):
    complete = [r for r in calibration["stations"] if r["complete"]]
    low_count = sum(r["successes"] / r["trials"] <= settings.low for r in complete)
    high_count = sum(r["successes"] / r["trials"] >= settings.high for r in complete)
    report = {
        "scene_id": scene["scene_id"],
        "task": scene["task"]["operation"],
        "stations": len(scene["stations"]),
        "complete_stations": len(complete),
        "incomplete_stations": len(scene["stations"]) - len(complete),
        "low_stations": low_count,
        "high_stations": high_count,
        "candidate_episodes": len(choices),
        "eligible_episodes": 0,
        "written_episodes": 0,
        "exclusions": {},
        "initialization_rejections": {},
    }
    if not choices:
        report["exclusions"] = {_no_choice_reason(complete, low_count, high_count): low_count or 1}
    return report


def _eligible_episode(navigation, region, station, evidence, legal, subset, settings, exclusions, diagnostics):
    """One start station → its recovery region and difficulty, or None (reason counted in ``exclusions``)."""
    navigation.hold(station)
    base = station["base"]
    source_check = navigation.check_pose(base)
    start_cell = region.grid.cell(base[:2])
    diagnostic = {
        "station_id": station["station_id"],
        "conservative_start_blocked": start_cell is None or not bool(region.grid.free[start_cell]),
        "start_clearance_lower_bound_m": source_check.clearance_m,
    }
    diagnostics.append(diagnostic)
    if not source_check.valid:
        diagnostic["reason"] = source_check.reason
        exclusions[source_check.reason] += 1
        return None
    by_id = {s["station_id"]: s for s in legal}
    incompatible = [
        r["station_id"] for r in region.measured if r["high"] and not navigation.compatible(by_id[r["station_id"]])
    ]
    measured = [{**r, "high": r["high"] and r["station_id"] not in incompatible} for r in region.measured]
    episode_region = RecoveryRegion.build(region.grid, measured, settings, navigation=navigation)
    diagnostic["incompatible_recovery_stations"] = incompatible
    diagnostic["recovery_anchors"] = [p.anchor["station_id"] for p in episode_region.patches]
    planned = navigation.plan(base, episode_region.goals())
    diagnostic.update({k: v for k, v in planned.items() if k != "path"})
    if planned["path"] is None:
        exclusions[planned["reason"]] += 1
        return None
    metrics = difficulty(
        episode_region,
        {**station, "xy": base[:2], "base": base},
        legal,
        settings.local_candidate_radius_m,
        reference_path=planned["path"],
    )
    group = failure_family([r for r in subset if r["station_id"] == station["station_id"]])
    return EligibleEpisode(station, evidence, metrics, group, episode_region)


def _eligible_episodes(sim, scene, calibration, choices, subset, settings, navigation_settings, report, rng):
    """Stations whose source initialization is valid and that reach a measured recovery region."""
    region, legal, report["initialization_rejections"], report["navigation_footprint"] = _navigation_region(
        sim, scene, calibration, settings
    )
    report["navigation_extent"] = "source_room_floor"
    report["navigation_method"] = "geometry_soft_astar"
    report["navigation_diagnostics"] = []
    legal_ids = {s["station_id"] for s in legal}
    exclusions, eligible = Counter(), []
    navigation = None
    if region is not None:
        navigation = GeometryNavigation(
            sim, region.grid, legal, navigation_settings, budget_s=min(120.0, settings.wall_timeout_s)
        )
    try:
        for station, evidence in choices:
            if station["station_id"] not in legal_ids:
                exclusions["initialization_invalid"] += 1
                continue
            episode = _eligible_episode(
                navigation,
                region,
                station,
                evidence,
                legal,
                subset,
                settings,
                exclusions,
                report["navigation_diagnostics"],
            )
            if episode is not None:
                eligible.append(episode)
    finally:
        if navigation is not None:
            navigation.close()
    report["eligible_episodes"] = len(eligible)
    if settings.max_episodes_per_scene and len(eligible) > settings.max_episodes_per_scene:
        indices = sorted(rng.choice(len(eligible), settings.max_episodes_per_scene, replace=False).tolist())
        exclusions["explicit_seeded_subsample"] = len(eligible) - len(indices)
        eligible = [eligible[i] for i in indices]
    report["exclusions"] = dict(exclusions)
    return eligible


def _write_episodes(root, sim, scene, eligible, settings, episodes, oracle):
    """Freeze the source scene once and add one episode (and its oracle region) per eligible start."""
    relative = Path("scene-files") / scene["scene_id"]
    sim.restore_snapshot(Path(scene["scene_path"]) / "initial.npz")
    sim.freeze(root / relative)
    for item in eligible:
        eid = f"e{len(episodes):06d}"
        oracle["regions"][eid] = item.region.to_dict()
        episodes.append(
            {
                "episode_id": eid,
                "scene_id": scene["scene_id"],
                "scene": str(relative),
                "task": scene["task"],
                "station": item.station,
                "seed": settings.seed,
            }
        )
        oracle["episodes"][eid] = {
            "region_id": eid,
            "scene_id": scene["scene_id"],
            "object_id": scene["task"]["instance_id"],
            "task_id": scene["task"]["task_id"],
            "task": scene["task"]["operation"],
            "case": scene.get("case", scene.get("case_name", "unknown")),
            "start_successes": item.evidence["successes"],
            "start_trials": item.evidence["trials"],
            "start_success_rate": item.evidence["success_rate"],
            **item.group,
            "difficulty": item.metrics,
        }


def _load_reference(source, calibration_dir):
    saved = read_json(calibration_dir / "config.json")
    if Path(saved["dataset"]).resolve() != source:
        raise ValueError("calibration source dataset mismatch")
    if saved["config"]["operation_backend"] != "curobo":
        raise ValueError("v0.1 construction requires cuRobo reference calibration")
    return saved


def build(dataset, output, *, calibration_dir, config_path=None):
    settings = load_benchmark_config(config_path) if config_path else BenchmarkConfig()
    source = Path(dataset).resolve()
    calibration_dir = Path(calibration_dir).resolve()
    saved = _load_reference(source, calibration_dir)
    reference = saved["config"]
    navigation_settings = NavigationSettings.from_config(
        settings,
        speed=reference.get("navigation_speed_m_s", 0.2),
        yaw_speed=reference.get("navigation_yaw_speed_rad_s", 0.4),
    )
    trials = rows(calibration_dir / "trials.jsonl")
    scenes = load_scenes(source)
    scene_ids = {s["scene_id"] for s in scenes}
    if any(r["scene_id"] not in scene_ids for r in trials):
        raise ValueError("calibration contains a scene outside the source dataset")
    root = create_run(
        output,
        {
            "schema_version": SCHEMA,
            "dataset": str(source),
            "navigation_protocol": NAVIGATION_PROTOCOL,
            "navigation_settings": asdict(navigation_settings),
            "calibration": str(calibration_dir),
            "config": asdict(settings),
            "reference_execution": reference,
            "success_rate_semantics": "task completion from calibrated initial station; base motion allowed by reference protocol",
        },
    )
    episodes, oracle, reports = [], {"regions": {}, "episodes": {}}, []
    progress = Progress(logger)
    started = time.monotonic()
    rng = np.random.default_rng(settings.seed)
    completed = False
    try:
        for scene in scenes:
            sid = scene["scene_id"]
            subset = [r for r in trials if r["scene_id"] == sid]
            binding = {"scene_id": sid, "task_id": scene["task"]["task_id"]}
            calibration = reference_calibration(
                scene["stations"], subset, reference["trials_per_station"], binding, reference["operation_backend"]
            )
            choices = select_starts(scene["stations"], calibration, binding, low=settings.low, high=settings.high)
            report = _scene_report(scene, calibration, choices, settings)
            reports.append(report)
            if not choices:
                continue
            snapshot_robot = read_json(Path(scene["scene_path"]) / "config.json")["robot"]
            if saved["profiles"][sid]["robot"] != snapshot_robot:
                raise ValueError("source robot profile does not match calibration: " + sid)
            scene["robot_config"] = robot_config(snapshot_robot)
            with open_scene(scene) as sim:
                eligible = _eligible_episodes(
                    sim, scene, calibration, choices, subset, settings, navigation_settings, report, rng
                )
                if eligible:
                    _write_episodes(root, sim, scene, eligible, settings, episodes, oracle)
                    report["written_episodes"] = len(eligible)
            progress.update("构建 bench 场景=%s/%s episode=%s 当前=%s", len(reports), len(scenes), len(episodes), sid)
        completed = True
    finally:
        write_rows(root / "episodes.jsonl", episodes)
        write_json(root / "oracle.json", oracle)
        write_json(
            root / "summary.json",
            {
                "schema_version": SCHEMA,
                "status": "complete" if completed else "incomplete",
                "source_scenes": len(scenes),
                "processed_scenes": len(reports),
                "episodes": len(episodes),
                "candidate_episodes": sum(r["candidate_episodes"] for r in reports),
                "eligible_episodes": sum(r["eligible_episodes"] for r in reports),
                "new_calibration_rollouts": 0,
                "scenes": reports,
                "elapsed_s": time.monotonic() - started,
            },
        )
        progress.update(
            "benchmark 构建收尾 场景=%s/%s 已写入episode=%s 输出=%s",
            len(reports),
            len(scenes),
            len(episodes),
            root,
            force=True,
        )
    benchmark_view(root)
    return root
