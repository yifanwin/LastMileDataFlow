"""Evaluate a policy on a built last-mile benchmark: one isolated episode per benchmark entry."""

from contextlib import contextmanager
import logging
from dataclasses import asdict, replace
from pathlib import Path
from lastmile_dataflow.runtime.execution_config import CollectionConfig
from lastmile_dataflow.robots.registry import robot_config
from lastmile_dataflow.data.evaluation.benchmark import BenchmarkConfig
from lastmile_dataflow.data.evaluation.benchmark import summarize
from lastmile_dataflow.data.evaluation.recovery import RecoveryRegion, motion_diagnostics
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.construction.benchmark_scene import native_scene
from lastmile_dataflow.runtime.execution.vla import run_vla_attempt
from lastmile_dataflow.scenes.dataset import open_scene
from lastmile_dataflow.policies.config import backend, check_profile, create_policy, load_policy_config
from lastmile_dataflow.utils.io import create_run, read_json, rows, write_json, append_row
from lastmile_dataflow.utils.logging_config import Progress, trial_logging
from lastmile_dataflow.data.visualization.benchmark import evaluation_view

logger = logging.getLogger(__name__)


@contextmanager
def _episode_sim(snapshot, robot, episode, selected_backend, policy_path):
    """MolmoBot runs in a native-control copy of the scene; π0.5 uses the snapshot directly."""
    scene = {"scene_path": snapshot, "robot_config": robot, "task": episode["task"], "scene_id": episode["scene_id"]}
    with open_scene(scene) as sim:
        if selected_backend != "molmobot":
            check_profile(robot, "pi05", policy_path)
            yield sim
            return
        native = native_scene(sim)
        native.scene_id = episode["scene_id"]
        try:
            yield native
        finally:
            native.close()


def _evaluation_configs(saved, settings, selected_backend, policy_path, sim, root, seed):
    reference = saved["reference_execution"]
    width, height = (1024, 576) if selected_backend == "molmobot" else (reference["width"], reference["height"])
    config = ExecutionConfig(
        operation_backend=selected_backend,
        operation_base_mode="policy_relative",
        policy_config=str(policy_path),
        width=width,
        height=height,
        radius_m=reference["radius_m"],
        attempt_timeout_s=settings.wall_timeout_s,
        third_person_enabled=True,
        open_hold_s=reference.get("open_hold_s", 1.0),
    )
    camera = "head" if selected_backend == "molmobot" else "observation"
    collection = CollectionConfig(
        output_dir=str(root),
        width=width,
        height=height,
        seed=seed,
        max_steps=settings.horizon,
        record_video=True,
        record_images=False,
        video_cameras={sim.robot.observation_camera: camera, "third_person_camera": "third_person"},
    )
    return config, collection


def _unsupported(eid, selected_backend, robot):
    return {
        "episode_id": eid,
        "status": "unsupported_policy",
        "reason": selected_backend + " policy does not support " + robot.name,
        "recovery_success": None,
        "recording_complete": False,
        "executed_steps": 0,
    }


def evaluate(benchmark, output, policy_path):
    benchmark = Path(benchmark).resolve()
    episodes = rows(benchmark / "episodes.jsonl")
    if not episodes:
        raise ValueError("no benchmark episodes")
    saved = read_json(benchmark / "config.json")
    settings = BenchmarkConfig(**saved["config"])
    oracle = read_json(benchmark / "oracle.json")
    regions = {key: RecoveryRegion.from_dict(value) for key, value in oracle["regions"].items()}
    policy_config = load_policy_config(policy_path)
    selected_backend = backend(policy_path)
    root = create_run(
        output,
        {
            "benchmark": str(benchmark),
            "policy": asdict(policy_config),
            "seed": settings.seed,
            "horizon": settings.horizon,
            "wall_timeout_s": settings.wall_timeout_s,
            "reference_calibration": saved["calibration"],
            "scene_profiles": {},
        },
    )
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    results, profiles = [], {}
    policy = None
    progress = Progress(logger)
    try:
        for episode in episodes:
            eid = episode["episode_id"]
            snapshot = benchmark / episode["scene"]
            robot = robot_config(read_json(snapshot / "config.json")["robot"])
            if robot.name != policy_config.embodiment:
                record = _unsupported(eid, selected_backend, robot)
                append_row(root / "results.jsonl", record)
                results.append(record)
                continue
            with _episode_sim(snapshot, robot, episode, selected_backend, policy_path) as sim:
                profiles[episode["scene_id"]] = asdict(sim.robot.config)
                if policy is None:
                    policy = create_policy(policy_path, logs / f"{root.name}-policy.log")
                    policy.config = replace(policy.config, task_horizon=settings.horizon)
                config, collection = _evaluation_configs(
                    saved, settings, selected_backend, policy_path, sim, root, episode["seed"]
                )
                with trial_logging(f"episode={eid} scene={episode['scene_id']}", progress):
                    result = run_vla_attempt(
                        sim,
                        episode["task"],
                        episode["station"],
                        config,
                        collection,
                        root=root,
                        attempt_id=eid,
                        seed=episode["seed"],
                        policy=policy,
                    )
            record = {
                key: result.get(key)
                for key in (
                    "status",
                    "reason",
                    "executed_steps",
                    "recording_complete",
                    "rollout_id",
                    "execution_termination",
                    "task_outcome",
                    "failure_scope",
                )
            }
            record["episode_id"] = eid
            record.update(
                motion_diagnostics(
                    root / "rollouts" / (record["rollout_id"] or eid) / "trajectory.npz",
                    regions[oracle["episodes"][eid]["region_id"]],
                    settings,
                    recording_complete=bool(record["recording_complete"]),
                )
            )
            append_row(root / "results.jsonl", record)
            results.append(record)
            progress.update(
                "benchmark 已记录episode=%s/%s 当前=%s status=%s", len(results), len(episodes), eid, record["status"]
            )
            if record.get("failure_scope") == "shared":
                break
    finally:
        if policy is not None:
            policy.close()
        write_json(root / "summary.json", summarize(episodes, results, oracle["episodes"]))
        run_config = read_json(root / "config.json")
        run_config["scene_profiles"] = profiles
        write_json(root / "config.json", run_config)
        evaluation_view(root)
        progress.update("benchmark 评测收尾 已记录episode=%s/%s 输出=%s", len(results), len(episodes), root, force=True)
    return root
