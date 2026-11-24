"""Pick each geometry-checked grasp in the scene with a mocap-held native gripper, nominal and perturbed."""

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import logging
import time
from pathlib import Path

import numpy as np

from lastmile_dataflow.operation.grasp_generation import PickSettings, build_scene, perturb, simulate_pick
from lastmile_dataflow.utils.io import create_run, read_json, write_json

STATE = {}


def setup(scene, metadata, target, robot_config):
    STATE["scene"] = build_scene(scene, metadata, target, robot_config)
    STATE["qpos"] = STATE["scene"].data.qpos.copy()


def run(job):
    row, poses, approach, settings = job
    scene = STATE["scene"]
    return row, [simulate_pick(scene, pose, approach, settings, STATE["qpos"]) for pose in poses]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", type=Path, required=True, help="generate_grasps.py run directory")
    parser.add_argument("--perturbations", type=int, default=4)
    parser.add_argument("--position-noise-m", type=float, default=0.005)
    parser.add_argument("--angle-noise-deg", type=float, default=5.0)
    parser.add_argument("--approach-max-m", type=float, default=PickSettings.approach_max_m)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    geometry = read_json(args.geometry / "config.json")
    output = create_run(args.output, {**{k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                                      "geometry_config": geometry})
    settings = PickSettings(approach_max_m=args.approach_max_m)
    with np.load(args.geometry / "grasps.npz") as source:
        world, clear = source["world_transforms"], source["approach_clear_m"]
    rng = np.random.default_rng(args.seed)
    jobs = []
    for row in range(len(world)):
        poses = [world[row]] + [perturb(world[row], rng, args.position_noise_m, np.radians(args.angle_noise_deg))
                                for _ in range(args.perturbations)]
        jobs.append((row, poses, float(min(clear[row], settings.approach_max_m)), settings))
    started = time.monotonic()
    results = {}
    with ProcessPoolExecutor(args.workers, initializer=setup, initargs=(
            geometry["scene"], geometry["metadata"], geometry["target"], geometry["robot_config"])) as pool:
        for count, (row, outcome) in enumerate(pool.map(run, jobs, chunksize=4), 1):
            results[row] = outcome
            if count % 100 == 0:
                logging.info("Simulated %d/%d grasps", count, len(jobs))
    order = sorted(results)
    success = np.array([[o["success"] for o in results[r]] for r in order])
    np.savez(output / "physics.npz", rows=np.array(order), success=success,
             held_s=np.array([[o["held_s"] for o in results[r]] for r in order]),
             peak_bottom_m=np.array([[o["peak_bottom_m"] for o in results[r]] for r in order]))
    with (output / "trials.jsonl").open("w") as stream:
        for r in order:
            stream.write(json.dumps({"row": int(r), "trials": results[r]}) + "\n")
    robustness = success.mean(1)
    summary = dict(grasps=len(order), trials_per_grasp=success.shape[1], nominal_success=int(success[:, 0].sum()),
                   robustness_histogram={f"{k}/{success.shape[1]}": int((success.sum(1) == k).sum())
                                         for k in range(success.shape[1] + 1)},
                   mean_robustness=float(robustness.mean()), elapsed_s=time.monotonic() - started,
                   settings=vars(settings) if hasattr(settings, "__dict__") else str(settings))
    write_json(output / "summary.json", summary)
    print(summary)


if __name__ == "__main__":
    main()
