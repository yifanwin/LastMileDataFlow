"""Bounded calibration and continuous collection on one accepted scene.

Optional benchmark execution requires an existing source cuRobo calibration.
No synthetic recovery pair, automatic smoke fallback or relocation probe.
"""

import argparse
import os
from dataclasses import replace
from pathlib import Path

from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.utils.io import create_run, rows, write_json
from lastmile_dataflow.runtime.execution_config import load_execution_config
from lastmile_dataflow.workflows.collection import collect_lastmile, evaluate_stations
from lastmile_dataflow.workflows.lastmilebench import build, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/execution/curobo_v2.json"))
    parser.add_argument("--trials-per-station", type=int, default=2)
    parser.add_argument("--benchmark-calibration", type=Path)
    parser.add_argument("--policy-config", type=Path, default=Path("configs/policies/molmobot.json"))
    args = parser.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu), MUJOCO_GL="egl")
    config = replace(load_execution_config(args.config), trials_per_station=args.trials_per_station)
    root = create_run(
        args.output,
        {
            "dataset": str(args.dataset.resolve()),
            "scene_id": args.scene_id,
            "gpu": args.gpu,
            "seed": config.seed,
        },
    )
    options = {"samples": [args.scene_id], "gpu_ids": [args.gpu]}
    recording = CollectionConfig(
        width=config.width, height=config.height, seed=config.seed, record_video=False
    )
    calibration = evaluate_stations(
        args.dataset, args.assets_dir, config, recording, root, run_id="calibration", **options
    )
    collection = collect_lastmile(
        args.dataset,
        args.assets_dir,
        config,
        recording,
        root,
        run_id="collection",
        calibration=calibration,
        **options,
    )
    result = {"calibration": str(calibration), "collection": str(collection)}
    write_json(root / "results.json", result)
    if args.benchmark_calibration:
        bench = build(
            args.dataset,
            root / "bench",
            gpu_ids=[args.gpu],
            calibration_dir=args.benchmark_calibration,
            config_path="configs/benchmark/v0.1.json",
        )
        result["benchmark"] = str(bench)
        if rows(bench / "episodes.jsonl"):
            result["evaluation"] = str(
                evaluate(
                    bench,
                    root / "evaluation",
                    args.policy_config,
                    gpu_ids=[args.gpu],
                    log_dir=Path("outputs/logs"),
                )
            )
        else:
            result["evaluation"] = "not_run: no qualified difficult starts"
        write_json(root / "results.json", result)


if __name__ == "__main__":
    main()
