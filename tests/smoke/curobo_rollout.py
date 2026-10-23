"""Explicit V2 rollout on a scene-dataset; excluded from unittest discovery."""
from contextlib import closing

import argparse
import os
from dataclasses import asdict
from pathlib import Path

from lastmile_dataflow.config import CollectionConfig, RobotConfig, read_config
from lastmile_dataflow.utils.io import create_run, write_json
from lastmile_dataflow.runtime.execution.curobo import run_curobo_attempt
from lastmile_dataflow.runtime.execution_config import load_execution_config
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.dataset import load_scenes
from lastmile_dataflow.scenes.tasks.discovery import grasp_candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--station", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/execution/curobo_v2.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    args = parser.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu), MUJOCO_GL="egl")
    config = load_execution_config(args.config)
    scene = next(row for row in load_scenes(args.dataset, [args.scene_id]))
    station = next(row for row in scene["stations"] if row["station_id"] == args.station)
    robot = RobotConfig(**read_config(Path(scene["scene_path"]) / "config.json")["robot"])
    collection = CollectionConfig(
        record_video=False, width=config.width, height=config.height, seed=config.seed
    )
    root = create_run(
        args.output, {"config": asdict(config), "seed": config.seed, "scene": scene["scene_id"]}
    )
    sim = Simulation.from_snapshot(scene["scene_path"], robot, target=scene["task"]["target_body"])
    sim.scene_id = scene["scene_id"]
    with closing(sim):
        result = run_curobo_attempt(
            sim,
            scene["task"],
            station,
            grasp_candidates(scene["task"], args.assets_dir),
            args.assets_dir,
            config,
            collection,
            root=root,
            attempt_id="smoke",
            seed=config.seed,
        )
        write_json(root / "result.json", result)
        print(result["status"])


if __name__ == "__main__":
    main()
