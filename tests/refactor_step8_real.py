"""Bounded real-scene acceptance run after build-scenes; no invented calibration."""

import argparse
from dataclasses import asdict, replace
import os
from pathlib import Path
import traceback

os.environ.setdefault("MUJOCO_GL", "egl")

from lastmile_dataflow.config import CollectionConfig, RobotConfig, read_config
from lastmile_dataflow.io import create_run, read_json, write_json, rows, append_row
from lastmile_dataflow.scenes.dataset import load_scenes, select_stations
from lastmile_dataflow.scenes.dataset_config import SceneStationConfig, load_scene_robot
from lastmile_dataflow.scenes.stations.sampling import sample_stations
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.construction.benchmark_scene import native_scene
from lastmile_dataflow.runtime.execution_config import load_execution_config
from lastmile_dataflow.workflows.collection import evaluate_stations, collect_lastmile
from lastmile_dataflow.workflows.lastmilebench import build, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--gpu", type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    os.environ.update(
        CUDA_VISIBLE_DEVICES=str(args.gpu),
        MUJOCO_EGL_DEVICE_ID=str(args.gpu),
        MUJOCO_GL="egl",
        OMP_NUM_THREADS="1",
    )
    dataset = (
        args.dataset.resolve()
        if args.dataset
        else root / "scene_datasets/scenes-bounded"
    )
    scenes = load_scenes(dataset)
    if not scenes:
        raise RuntimeError(
            "no accepted augmented scene; do not pretend the downstream test ran"
        )
    result = {}

    def run(name, action):
        try:
            output = action()
            result[name] = {"status": "executed", "output": str(output)}
            print(name, output, flush=True)
            return output
        except Exception as exc:
            traceback.print_exc()
            result[name] = {
                "status": "error",
                "reason": type(exc).__name__ + ": " + str(exc),
            }
            return None
        finally:
            write_json(root / "results.json", result)

    config = replace(
        load_execution_config("configs/execution/curobo_v2.json"),
        seed=20261014,
        trials_per_station=2,
        start_count=1,
        max_s1_candidates=1,
        operations=["pick"],
        max_operation_plans=2,
        num_ik_seeds=8,
        num_trajopt_seeds=2,
        attempt_timeout_s=300.0,
        third_person_enabled=False,
    )
    write_json(root / "configs/curobo.json", asdict(config))
    recording = CollectionConfig(
        max_steps=1000, record_video=False, record_images=False
    )
    calibration = run(
        "curobo",
        lambda: evaluate_stations(
            dataset,
            args.assets_dir,
            config,
            recording,
            root,
            run_id="curobo",
            gpu_ids=[args.gpu],
        ),
    )
    if calibration:
        run(
            "lastmile",
            lambda: collect_lastmile(
                dataset,
                args.assets_dir,
                config,
                recording,
                root,
                run_id="lastmile",
                gpu_ids=[args.gpu],
                calibration=calibration,
            ),
        )

    # Optional continuous-execution probe: not an automatically selected difficult S0/S1.
    if calibration and not rows(root / "lastmile" / "episodes.jsonl"):

        def continuity_probe():
            from lastmile_dataflow.planning.astar import scene_grid, search, task_support_obstacles
            from lastmile_dataflow.scenes.tasks.discovery import robot_footprint, grasp_candidates
            from lastmile_dataflow.runtime.execution.curobo import run_curobo_attempt

            scene = scenes[0]
            robot = RobotConfig(
                **read_config(Path(scene["scene_path"]) / "config.json")["robot"]
            )
            sim = Simulation.from_snapshot(
                scene["scene_path"], robot, target=scene["task"]["target_body"]
            )
            sim.scene_id = scene["scene_id"]
            out = create_run(
                root / "continuity-smoke",
                {
                    "seed": config.seed,
                    "config": asdict(config),
                    "selection": "geometric_pair_only_not_calibrated_difficulty",
                },
            )
            try:
                grid = scene_grid(
                    sim,
                    scene["task"]["anchor_world"],
                    config.radius_m,
                    config.map_resolution_m,
                    robot_footprint(sim)["radius_m"],
                    config.navigation_margin_m,
                    support_obstacles=task_support_obstacles(sim, scene["task"]),
                )
                pair = None
                for a in scene["stations"]:
                    for b in scene["stations"]:
                        if a["station_id"] == b["station_id"]:
                            continue
                        path = search(grid, a["xy"], b["xy"])
                        if path:
                            pair = (a, b, path)
                            break
                    if pair:
                        break
                if not pair:
                    write_json(
                        out / "result.json",
                        {"status": "path_unavailable", "robot_executed": False},
                    )
                else:
                    a, b, path = pair
                    actual = run_curobo_attempt(
                        sim,
                        scene["task"],
                        a,
                        grasp_candidates(scene["task"], args.assets_dir),
                        args.assets_dir,
                        config,
                        recording,
                        root=out, attempt_id="continuity",
                        seed=config.seed,
                        path=path,
                        goal=b,
                    )
                    write_json(
                        out / "result.json",
                        {
                            **actual,
                            "s0": a["station_id"],
                            "s1": b["station_id"],
                            "selection": "geometric_pair_only_not_calibrated_difficulty",
                        },
                    )
                return out
            finally:
                sim.close()

        run("continuity_smoke", continuity_probe)

    # The native policy scene is explicit: same non-robot scene, changed robot controls/cameras.
    geometry = {
        **read_json(dataset / "config.json")["geometry"],
        "width": 1024,
        "height": 576,
    }
    native = create_run(
        root / "native-scenes", {"geometry": geometry, "seed": 20261014}
    )
    for scene in scenes:
        parent = Simulation.from_snapshot(
            scene["scene_path"],
            RobotConfig(
                **read_config(Path(scene["scene_path"]) / "config.json")["robot"]
            ),
            target=scene["task"]["target_body"],
        )
        sim = native_scene(parent)
        try:
            stations, _ = select_stations(
                sample_stations(sim, scene["task"], SceneStationConfig(**geometry)),
                scene["task"]["anchor_world"],
                limit=3,
            )
            location = Path("scene-files") / scene["scene_id"]
            sim.freeze(native / location)
            append_row(
                native / "scenes.jsonl",
                {
                    "scene_id": scene["scene_id"],
                    "status": "accepted",
                    "scene": str(location),
                    "task": scene["task"],
                    "stations": stations,
                    "placements": scene["placements"],
                    "case": scene["case"],
                },
            )
        finally:
            sim.close()
            parent.close()
    policy = root / "configs/policy.json"
    vla = replace(
        config,
        operation_backend="molmobot",
        operation_base_mode="policy_relative",
        policy_config=str(policy),
        width=1024,
        height=576,
    )
    write_json(root / "configs/vla.json", asdict(vla))
    vla_calibration = run(
        "molmobot",
        lambda: evaluate_stations(
            native,
            args.assets_dir,
            vla,
            replace(recording, max_steps=400),
            root,
            run_id="molmobot",
            gpu_ids=[args.gpu],
        ),
    )
    if vla_calibration:
        bench = run(
            "hard_bench",
            lambda: build(
                native,
                root / "hard-bench",
                policy,
                gpu_ids=[args.gpu],
                calibration_dir=vla_calibration,
                starts_per_sample=1,
                seed=20261014,
                horizon=400,
                wall_timeout_s=300.0,
                radius_m=geometry["radius_m"],
            ),
        )
        if bench and rows(bench / "episodes.jsonl"):
            run(
                "hard_bench_evaluation",
                lambda: evaluate(
                    bench,
                    root / "hard-bench-eval",
                    policy,
                    gpu_ids=[args.gpu],
                    log_dir=Path("outputs/logs"),
                ),
            )
        else:
            result["hard_bench_evaluation"] = {
                "status": "not_run",
                "reason": "no calibrated difficult S0 or incomplete calibration",
            }
            smoke = run(
                "uncalibrated_bench_smoke",
                lambda: build(
                    native,
                    root / "bench-smoke",
                    policy,
                    gpu_ids=[args.gpu],
                    allow_uncalibrated=True,
                    seed=20261014,
                    horizon=400,
                    wall_timeout_s=300.0,
                    radius_m=geometry["radius_m"],
                ),
            )
            if smoke:
                run(
                    "uncalibrated_bench_evaluation",
                    lambda: evaluate(
                        smoke,
                        root / "bench-smoke-eval",
                        policy,
                        gpu_ids=[args.gpu],
                        log_dir=Path("outputs/logs"),
                    ),
                )
    # Move one actual MJB snapshot and explicitly update its model locator.
    import shutil

    scene = scenes[0]
    relocated = root / "relocated-scene"
    shutil.copytree(scene["scene_path"], relocated)
    saved = read_json(relocated / "config.json")
    robot_copy = root / "relocated-robot.xml"
    shutil.copyfile(saved["robot"]["model_path"], robot_copy)
    robot_config = {
        **saved["robot"],
        "data_root": str(root),
        "model_path": robot_copy.name,
    }
    write_json(root / "configs/relocated-robot.json", robot_config)
    saved["robot"] = asdict(load_scene_robot(root / "configs/relocated-robot.json"))
    write_json(relocated / "config.json", saved)
    sim = Simulation.from_snapshot(
        relocated, RobotConfig(**saved["robot"]), target=scene["task"]["target_body"]
    )
    try:
        result["relocation"] = {
            "status": "loaded",
            "nq": sim.model.nq,
            "target": sim.model.body(sim.target_id).name,
            "scope": "compiled MJB reload; not portable XML/mesh reconstruction",
        }
    finally:
        sim.close()
    write_json(root / "results.json", result)


if __name__ == "__main__":
    main()
