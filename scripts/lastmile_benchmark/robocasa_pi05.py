"""π0.5 pick trials on frozen RoboCasa365 scenes from RL-grid stations (run in .venv-robocasa)."""

import argparse
from pathlib import Path

from lastmile_dataflow.utils.gpu import select_devices
from lastmile_dataflow.utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", type=Path, required=True, help="RoboCasa scene run from scripts/scenes/build.py")
    parser.add_argument("--output", type=Path, required=True, help="new run directory")
    parser.add_argument("--gpu", type=int, nargs="+", required=True, help="physical GPUs, one worker and π0.5 service each")
    parser.add_argument("--policy-config", type=Path, default=Path("configs/policies/pi05_panda_omron.yaml"))
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    parser.add_argument("--horizon", type=int, default=400, help="control steps per episode (20 Hz)")
    parser.add_argument("--prompt", default="Pick up the cup.")
    parser.add_argument("--max-stations-per-scene", type=int, default=0, help="0 keeps every valid station")
    parser.add_argument("--evidence", type=Path, help="fixed station tables (<evidence>/<scene>/stations.jsonl); --scenes is then the scenes dir")
    parser.add_argument("--scene-names", nargs="+", default=[], help="paired scenes for --evidence")
    parser.add_argument("--stations", nargs="*", default=[], help="station ids for --evidence; default all shared visible")
    parser.add_argument("--free-base", action="store_true", help="reference only: do not lock the base (--evidence)")
    args = parser.parse_args()
    select_devices(args.gpu)
    # MuJoCo reads the EGL binding at import; robosuite/π0.5 modules are imported after binding.
    from lastmile_dataflow.lastmile_benchmark.robocasa_policy import run_station_table, run_trials

    setup_logging()
    if args.evidence:
        print(run_station_table(args.scenes, args.evidence, args.scene_names, policy_config=args.policy_config, output=args.output,
                                gpus=args.gpu, seeds=args.seeds, horizon=args.horizon, prompt=args.prompt, station_ids=args.stations, fixed_base=not args.free_base))
        return
    summary = run_trials(
        args.scenes,
        policy_config=args.policy_config,
        output=args.output,
        gpus=args.gpu,
        seeds=args.seeds,
        horizon=args.horizon,
        prompt=args.prompt,
        max_stations_per_scene=args.max_stations_per_scene,
    )
    print(summary)


if __name__ == "__main__":
    main()
