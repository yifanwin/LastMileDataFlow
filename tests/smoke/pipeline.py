"""Bounded calibration and continuous collection on one accepted scene.

Optional benchmark execution requires an existing source cuRobo calibration.
No synthetic recovery pair, automatic smoke fallback or relocation probe.
"""

import argparse
from pathlib import Path

import yaml

from lastmile_dataflow.utils.io import read_config
from lastmile_dataflow.station_map.config import StationRun
from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.io import create_run, rows, write_json
from lastmile_dataflow.lastmile_dataset.collect import run_collection
from lastmile_dataflow.station_map.planner_trials.build import run_station_map
from lastmile_dataflow.lastmile_benchmark.build import build
from lastmile_dataflow.lastmile_benchmark.evaluate import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--config", type=Path, default=Path("configs/station_map/rby1_curobo.yaml"))
    parser.add_argument("--trials-per-station", type=int, default=2)
    parser.add_argument("--benchmark-calibration", type=Path)
    parser.add_argument("--policy-config", type=Path, default=Path("configs/policies/molmobot.yaml"))
    args = parser.parse_args()
    select_devices([args.gpu])
    execution = {**read_config(args.config), "trials_per_station": args.trials_per_station}
    root = create_run(
        args.output,
        {
            "dataset": str(args.dataset.resolve()),
            "scene_id": args.scene_id,
            "gpu": args.gpu,
            "seed": execution.get("seed", 42),
        },
    )
    (root / "execution.yaml").write_text(yaml.safe_dump(execution))
    run = StationRun(str(root / "execution.yaml"), assets_dir=str(args.assets_dir.resolve()))
    options = {"samples": [args.scene_id], "gpu_ids": [args.gpu]}
    calibration = run_station_map(args.dataset, run, root / "calibration", **options)
    collection = run_collection(args.dataset, run, root / "collection", calibration=calibration, **options)
    result = {"calibration": str(calibration), "collection": str(collection)}
    write_json(root / "results.json", result)
    if args.benchmark_calibration:
        bench = build(
            args.dataset,
            root / "bench",
            calibration_dir=args.benchmark_calibration,
        )
        result["benchmark"] = str(bench)
        if rows(bench / "episodes.jsonl"):
            result["evaluation"] = str(
                evaluate(
                    bench,
                    root / "evaluation",
                    args.policy_config,
                )
            )
        else:
            result["evaluation"] = "not_run: no qualified difficult starts"
        write_json(root / "results.json", result)


if __name__ == "__main__":
    main()
