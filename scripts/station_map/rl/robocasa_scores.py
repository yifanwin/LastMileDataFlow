"""Ours on RoboCasa scenes: a fitted quality.pt predicted on each exported scene's station table.

Two jsonl rows per (scene, legal station), both over the same full grasp pool (--grasps, 152 cup_3 grasps):
{method: "Ours", scene, station_id, score} with score the equal-grasp mean of the sigma-integrated success
probability (plan 04 main table), and method "Ours-max" with the best grasp of that station (existence view,
comparable to cuRobo's goalset). Rows also carry the ensemble spread and per-grasp values keyed by pool row.

  python scripts/station_map/rl/robocasa_scores.py --quality <fit>/quality.pt --gpu 0 \
    --scene kettle-far=<rl-scenes>/kettle-far --scene kettle-left-125mm=<rl-scenes>/kettle-left-125mm --output scores.jsonl
"""
import argparse
import json
import time
from pathlib import Path

import torch

from lastmile_dataflow.station_map.rl.config import resolve_config
from lastmile_dataflow.utils.gpu import bind_device


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--quality", type=Path, required=True)
    p.add_argument("--scene", action="append", required=True, help="NAME=exported RL scene directory")
    p.add_argument("--gpu", type=int, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--grasps", type=Path, default=Path("outputs/experiments/robocasa-station-eval/runs/"
                                                         "20261124-0024-cup3-grasp-pool-pitch/rl_transforms.npz"))
    p.add_argument("--pool-size", type=int, default=152)
    a = p.parse_args()
    bind_device(a.gpu)
    from lastmile_dataflow.station_map.rl.devices import VISIBLE_DEVICE
    from lastmile_dataflow.station_map.rl.env.environment import StationEnv
    from lastmile_dataflow.station_map.rl.networks import QualityMember
    from lastmile_dataflow.station_map.rl.quality import station_maps

    saved = torch.load(a.quality, map_location="cpu", weights_only=False)
    config = resolve_config(torch.load(saved["checkpoint"], map_location="cpu", weights_only=False)["metadata"]["config"])
    config["grasp"] = {**config["grasp"], "preprocessed_path": str(a.grasps.resolve()), "sample_count": a.pool_size}
    rows = []
    for item in a.scene:
        name, directory = item.split("=", 1)
        scene = {**config["scene"], "scene_xml": str(Path(directory, "scene.xml").resolve()),
                 "scene_metadata": str(Path(directory, "metadata.json").resolve())}
        env = StationEnv({**config, "scene": scene}, gpu=VISIBLE_DEVICE, seed=0, num_envs=1)
        try:
            members = []
            for state in saved["members"]:
                member = QualityMember().to(env.device)
                member.load_state_dict(state)
                members.append(member.eval())
            started = time.monotonic()
            maps, grasps = station_maps(env.task, members, env.device)
            elapsed = time.monotonic() - started
            if len(grasps) != a.pool_size:
                raise ValueError(f"scored {len(grasps)} grasps, expected the full pool of {a.pool_size}")
            pool_rows = [env.task.grasps[g]["row"] for g in grasps]
            for r in maps:
                common = {"scene": name, "station_id": r["station_id"], "map_s": elapsed}
                rows.append({"method": "Ours", **common, "score": r["q_phys_uniform_grasp"], "u": r["u_uniform_grasp"],
                             "r_by_pool_row": dict(zip(map(str, pool_rows), r["r_by_grasp"]))})
                rows.append({"method": "Ours-max", **common, "score": r["q_phys"], "u": r["u"],
                             "best_pool_row": pool_rows[grasps.index(r["best_grasp_id"])]})
        finally:
            env.close()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(json.dumps({"rows": len(rows), "scenes": [s.split("=")[0] for s in a.scene], "output": str(a.output)}))


if __name__ == "__main__":
    main()
