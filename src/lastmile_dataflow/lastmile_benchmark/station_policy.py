"""Repeated policy-only picks from target-facing stations in one frozen scene."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import mujoco
import numpy as np
import yaml

from lastmile_dataflow.operation.config import CollectionConfig, ExecutionConfig
from lastmile_dataflow.operation.outcomes.pick_state import RL_PICK_THRESHOLDS
from lastmile_dataflow.policies.config import check_profile
from lastmile_dataflow.policies.pi05.client import Pi05Client
from lastmile_dataflow.policies.pi05.config import load_config as load_policy_config
from lastmile_dataflow.robots.registry import load_robot_config, robot_config as parse_robot_config
from lastmile_dataflow.scenes.tasks import discover_tasks
from lastmile_dataflow.scenes.library import SceneStationConfig
from lastmile_dataflow.sim.model.geometry import body_pose
from lastmile_dataflow.scenes.tasks import local_bounds
from lastmile_dataflow.sim.simulation import Simulation
from lastmile_dataflow.sim.source import SceneSource
from lastmile_dataflow.station_map.rl.config import resolve_config
from lastmile_dataflow.station_map.rl.env.scene import RLSceneConfig
from lastmile_dataflow.station_map.rl.env.stations import check_stations
from lastmile_dataflow.utils.figures import plt, save_figure
from lastmile_dataflow.utils.io import REPO_ROOT, append_row, create_run, read_config, read_json, rows, write_json, write_rows
from .policy_rollout import run_vla_attempt

logger = logging.getLogger(__name__)
SEEDS = (42, 43, 44)


def _prepare_scene(config, robot_path, output):
    scene = RLSceneConfig(**config["scene"])
    robot = load_robot_config(robot_path)
    initial = deepcopy(robot.initial)
    initial.update(
        base_xyyaw=[-10.0, -10.0, 0.0], lift=scene.lift,
        arm_q={"right": list(scene.home)}, gripper_width={},
    )
    robot = replace(robot, initial=initial)
    source = SceneSource("val103-cup30", scene.asset(scene.scene_xml), scene.asset(scene.scene_metadata))
    sim = Simulation.from_source(source, robot, target=scene.target, robot_scale=scene.robot_scale)
    try:
        # Match RL settling, with the robot parked away from all candidate poses.
        for _ in range(round(config["environment"]["settling_s"] / scene.physics_dt)):
            sim.robot.hold()
            mujoco.mj_step(sim.model, sim.data)
        mujoco.mj_forward(sim.model, sim.data)
        sim.scene_id = source.scene_id
        stations = check_stations(sim, config["stations"], facing_only=True)
        lo, hi = local_bounds(sim, sim.target_id)
        center = (body_pose(sim, sim.target_id) @ np.r_[(lo + hi) / 2, 1.0])[:3]
        tasks, _ = discover_tasks(sim, sim.source_metadata, SceneStationConfig(operations=["pick"]))
        task = next(task for task in tasks if task["target_body"] == sim.model.body(sim.target_id).name)
        sim.freeze(output / "scene-inputs")
        return robot, stations, center.tolist(), task
    finally:
        sim.close()


def trial_specs(stations):
    """Stable station order, with all three independent seeds even after success."""
    for index, station in enumerate(stations):
        for seed in SEEDS:
            yield index, station, seed


def _station_root(output, index, station_id):
    # At most 20 station directories per bucket, and 20 buckets per group.
    return output / "attempts" / f"{index // 400:02d}" / f"{index // 20 % 20:02d}" / station_id


def run(output, *, gpus, scene_config, robot_config, policy_config, policy_python, horizon,
        attempt_timeout_s, station_limit, prepare_only):
    if not gpus or len(set(gpus)) != len(gpus):
        raise ValueError("select distinct physical GPUs")
    if horizon < 1 or attempt_timeout_s <= 0 or (station_limit is not None and station_limit < 1):
        raise ValueError("positive horizon, timeout and station limit required")
    config = resolve_config(read_config(scene_config))
    policy = load_policy_config(policy_config)
    if policy_python is not None:
        policy = replace(policy, python=str(policy_python.absolute()))
    policy = replace(policy, task_horizon=horizon)
    if not Path(policy.python).is_file():
        raise FileNotFoundError("pass --policy-python pointing to the existing π0.5 environment")
    output = Path(output).resolve()
    saved = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "sim_python": sys.executable, "mujoco_version": mujoco.__version__,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, timeout=10).strip(),
        "worktree_status": subprocess.check_output(["git", "status", "--short"], cwd=REPO_ROOT, text=True, timeout=10),
        "worktree": str(REPO_ROOT), "command": sys.argv,
        "scene_config": config, "policy": asdict(policy), "gpu_ids": list(gpus),
        "seeds": SEEDS, "success": RL_PICK_THRESHOLDS,
        "horizon": horizon, "attempt_timeout_s": attempt_timeout_s, "station_limit": station_limit,
    }
    create_run(output, saved)
    robot, stations, center, task = _prepare_scene(config, robot_config, output)
    check_profile(robot, "pi05", policy_config)
    selected = [station for station in stations if station["valid"]]
    if station_limit is not None:
        selected = selected[:station_limit]
    if not selected:
        raise ValueError("no valid target-facing stations")
    saved.update(robot=asdict(robot), target_center=center, task=task, valid_stations=len(selected), candidates=len(stations))
    write_json(output / "config.json", saved)
    write_rows(output / "stations.jsonl", stations)
    write_rows(output / "selected-stations.jsonl", selected)
    # Write resolved absolute paths so child processes never reinterpret input paths.
    (output / "policy.yaml").write_text(yaml.safe_dump({"backend": "pi05", "data_root": "/", **asdict(policy)}))
    (output / "logs").mkdir()
    logger.info("prepared %d candidates, %d valid stations, %d trials", len(stations), len(selected), 3 * len(selected))
    if prepare_only:
        return {"status": "prepared", "stations": len(selected), "expected_trials": 3 * len(selected)}
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    processes = []
    try:
        for worker_index, gpu in enumerate(gpus[:len(selected)]):
            log = (output / "logs" / f"worker-{gpu}.log").open("ab", buffering=0)
            command = [
                sys.executable, str(REPO_ROOT / "scripts/lastmile_benchmark/station_policy.py"),
                "--output", str(output), "--gpu", str(gpu), "--worker-index", str(worker_index),
            ]
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
            log.close()
            processes.append((gpu, process))
        write_json(output / "workers.json", [{"gpu": gpu, "pid": p.pid} for gpu, p in processes])
        for gpu, process in processes:
            code = process.wait()
            logger.info("worker GPU %d exited with code %d", gpu, code)
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
        summarize(output)
    return read_json(output / "summary.json")


def run_worker(output, *, gpu, worker_index):
    output = Path(output).resolve()
    saved = read_json(output / "config.json")
    stations = rows(output / "selected-stations.jsonl")
    workers = min(len(saved["gpu_ids"]), len(stations))
    assigned = [(i, station) for i, station in enumerate(stations) if i % workers == worker_index]
    robot = parse_robot_config(saved["robot"])
    sim = Simulation.from_snapshot(output / "scene-inputs", robot, target=saved["scene_config"]["scene"]["target"])
    sim.scene_id = "val103-cup30"
    execution = ExecutionConfig(
        operation_backend="pi05", operation_base_mode="policy_relative", policy_config=str(output / "policy.yaml"),
        width=256, height=256, attempt_timeout_s=saved["attempt_timeout_s"],
    )
    collection = CollectionConfig(record_video=True, record_images=False, width=256, height=256)
    policy = None
    completed = 0
    started = time.monotonic()
    try:
        task = saved["task"]
        policy = Pi05Client(output / "policy.yaml", output / "logs" / f"model-{gpu}.log")
        for local_index, row, seed in trial_specs([station for _, station in assigned]):
            index = assigned[local_index][0]
            initial = deepcopy(robot.initial)
            initial["base_xyyaw"] = [row["x"], row["y"], row["yaw"]]
            station = {**row, "xy": [row["x"], row["y"]], "initial": initial}
            root = _station_root(output, index, row["station_id"])
            result = run_vla_attempt(
                sim, task, station, execution, collection, root=root,
                attempt_id=f"seed-{seed}", seed=seed, policy=policy, pick_thresholds=RL_PICK_THRESHOLDS,
            )
            hands = (result.get("verdict") or {}).get("hands", {}).values()
            result.update(
                seed=seed, gpu=gpu, station_index=index,
                max_lift_m=max((hand.get("max_lift_m", 0.0) for hand in hands), default=None),
                max_held_s=max((hand.get("max_held_s", 0.0) for hand in hands), default=None),
            )
            append_row(output / f"trials-gpu-{gpu}.jsonl", result)
            completed += 1
            logger.info("GPU %d trial %d/%d station=%s seed=%d status=%s reason=%s", gpu, completed,
                        3 * len(assigned), row["station_id"], seed, result["status"], result.get("reason"))
            write_json(output / f"progress-gpu-{gpu}.json", {
                "completed": completed, "expected": 3 * len(assigned), "elapsed_s": time.monotonic() - started,
                "last_station": row["station_id"], "last_status": result["status"],
            })
            if result.get("failure_scope") == "shared" or (result.get("exception_detail") or {}).get("failure_scope") == "shared":
                raise RuntimeError("shared policy or GPU failure; see last trial result")
    finally:
        if policy is not None:
            policy.close()
        sim.close()
    return {"gpu": gpu, "completed": completed}


def summarize(output):
    output = Path(output).resolve()
    stations = rows(output / "selected-stations.jsonl")
    results = [row for path in sorted(output.glob("trials-gpu-*.jsonl")) for row in rows(path)]
    grouped = {}
    for row in results:
        grouped.setdefault(row["station_id"], []).append(row)
    records = []
    for station in stations:
        trials = grouped.get(station["station_id"], [])
        successes = sum(row["status"] == "success" for row in trials)
        clean = sum(row["status"] == "success" and row.get("trajectory_metrics", {}).get("forbidden_contact_count") == 0 for row in trials)
        records.append({**station, "trials": len(trials), "successes": successes, "success_rate": successes / 3,
                        "collision_free_successes": clean,
                        "missing_seeds": [seed for seed in SEEDS if seed not in {row["seed"] for row in trials}]})
    expected = 3 * len(stations)
    infrastructure = [row for row in results if row["status"] in ("infrastructure_error", "initialization_error", "episode_error", "recording_error")]
    summary = {
        "status": "complete" if len(results) == expected and all(not row["missing_seeds"] for row in records) else "incomplete",
        "expected_trials": expected, "recorded_trials": len(results), "valid_stations": len(stations),
        "successes": sum(row["successes"] for row in records),
        "success_rate": sum(row["successes"] for row in records) / expected if expected else None,
        "station_coverage": sum(row["successes"] > 0 for row in records) / len(stations) if stations else None,
        "collision_free_success_rate": sum(row["collision_free_successes"] for row in records) / expected if expected else None,
        "infrastructure_errors": len(infrastructure),
        "failure_reasons": dict(Counter(row.get("reason", row["status"]) for row in results if row["status"] != "success" and row not in infrastructure)),
        "infrastructure_reasons": dict(Counter(row.get("reason", row["status"]) for row in infrastructure)),
        "unfinished_stations": [row["station_id"] for row in records if row["missing_seeds"]],
    }
    write_rows(output / "station-results.jsonl", records)
    write_json(output / "summary.json", summary)
    analysis = output.parent.parent / "analysis" / output.name
    write_json(analysis / "summary.json", summary)
    if records:
        fig, ax = plt.subplots(figsize=(9, 7))
        finished = [row for row in records if not row["missing_seeds"]]
        pending = [row for row in records if row["missing_seeds"]]
        if finished:
            points = ax.scatter([r["x"] for r in finished], [r["y"] for r in finished],
                                c=[r["successes"] for r in finished], vmin=0, vmax=3, cmap="viridis", marker="s", s=60)
            fig.colorbar(points, ax=ax, ticks=range(4), label="Successful picks / 3 attempts")
        if pending:
            ax.scatter([r["x"] for r in pending], [r["y"] for r in pending], color="lightgray", marker="x", label="Incomplete")
        center = read_json(output / "config.json")["target_center"]
        ax.scatter(*center[:2], marker="*", s=160, color="red", label="Cup_30")
        ax.set(xlabel="World x (m)", ylabel="World y (m)", title="π0.5 target-facing initial stations; RL pick protocol")
        ax.set_aspect("equal")
        ax.legend()
        save_figure(fig, analysis / "station-success.png", dpi=150, bbox_inches="tight")
    return summary
