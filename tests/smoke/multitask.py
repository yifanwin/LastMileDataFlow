"""Explicit real-weight IPC smoke with synthetic RGB/state; no robot execution."""

import argparse
import os
import sys
from pathlib import Path

import numpy as np

from lastmile_dataflow.utils.io import create_run, write_json
from lastmile_dataflow.policies.molmobot import MolmoBotClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)
    os.environ["HF_HUB_OFFLINE"] = "1"
    root = Path(__file__).resolve().parents[2]
    run = create_run(
        args.output,
        {
            "checkpoint": str(args.checkpoint.resolve()),
            "tokenizer_path": str(args.tokenizer.resolve()),
            "gpu": args.gpu,
            "seed": args.seed,
        },
    )
    policy_config = {
        "data_root": str(args.checkpoint.resolve().parent),
        "checkpoint": str(args.checkpoint.resolve()),
        "tokenizer_path": str(args.tokenizer.resolve()),
        "python": sys.executable,
        "rpc_timeout_s": 120.0,
    }
    config_path = run / "policy.json"
    write_json(config_path, policy_config)
    log = root / "outputs/logs" / f"{run.name}-service.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    client = MolmoBotClient(config_path, log)
    try:
        observation = {
            camera: np.random.default_rng(args.seed + i).integers(0, 256, (576, 1024, 3), dtype=np.uint8)
            for i, camera in enumerate(("wrist_camera_r", "head_camera", "wrist_camera_l"))
        }
        observation["qpos"] = {
            group: np.zeros(size, np.float32)
            for group, size in [
                ("base", 3),
                ("left_arm", 7),
                ("left_gripper", 1),
                ("right_arm", 7),
                ("right_gripper", 1),
                ("torso", 6),
            ]
        }
        observation["object_image_points"] = {
            "door": {
                "head_camera": {
                    "points": np.array([[0.5, 0.5]], np.float32),
                    "num_points": np.array([1]),
                }
            }
        }
        results, actions = {}, {}
        for operation, prompt in [
            ("pick", "pick up the cup"),
            ("open", "open the door"),
        ]:
            observation["task"] = prompt
            response = client.action(
                observation,
                task={"operation": operation},
                attempt=operation,
                seed=args.seed,
                first=True,
            )
            assert response["action"].shape == (20,)
            assert np.isfinite(response["action"]).all()
            assert response["model_chunk_state"].shape == (22,)
            assert response["gripper_binary"] == (operation == "open")
            assert len(response["processed_images"]) == (4 if operation == "open" else 3)
            if operation == "open":
                assert "<points coords=" in response["model_prompt"]
            actions[operation] = response["action"]
            buffered = client.action(
                observation,
                task={"operation": operation},
                attempt=operation,
                seed=args.seed,
            )
            assert not buffered["new_chunk"] and buffered["chunk_index"] == 1
            results[operation] = {
                "finite_action": True,
                "inference_s": response["inference_s"],
                "chunk_index": response["chunk_index"],
                "buffer_next_index": buffered["chunk_index"],
                "gripper_binary": response["gripper_binary"],
            }
        np.savez(run / "actions.npz", **actions)
        write_json(
            run / "results.json",
            {"model": client.metadata, "operations": results, "robot_executed": False},
        )
        print("PASS: real checkpoint, pick/open actions and service/client buffering")
    finally:
        client.close()


if __name__ == "__main__":
    main()
